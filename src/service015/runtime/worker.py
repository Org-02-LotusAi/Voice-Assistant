"""LiveKit Agents runtime worker (console script `service015-runtime`).

One worker process registers with LiveKit under `LIVEKIT_AGENT_NAME` (explicit dispatch). Every launched Identity is one job in
its own spawned process: accepted with `identity=participant_identity`, `name=<display name>`, in room `default_room`.

The job rebuilds everything from the database on each start (`build_instructions` is called fresh, so self-facts are always the
authoritative stored ones) and writes presence straight into SQLite `runtime_status`. A job refuses to run an Identity without a
voice profile (never falls back to a default voice) and never produces speech that did not come from the LLM/greeting + TTS path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterable

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobExecutorType,
    JobProcess,
    JobRequest,
    ModelSettings,
    llm,
    stt,
    tts,
)
from livekit.agents.types import NOT_GIVEN
from livekit.agents.voice import room_io
from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    CloseEvent,
    ErrorEvent,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
)
from livekit.plugins import openai, silero

from service015.config import Settings, get_settings
from service015.db import connect, migrate
from service015.errors import DomainError
from service015.identities_repo import get_identity
from service015.resolver import ResolvedIdentity
from service015.runtime import presence as P
from service015.runtime.latency import LatencyRecorder
from service015.selfcontext import build_instructions
from service015.stt.moonshine import MoonshineSTT, load_transcriber
from service015.voice.engine import VoiceEngineClient
from service015.voice.livekit_tts import ChatterboxTTS

log = logging.getLogger("service015.runtime")

HEARTBEAT_S = 5.0
HUMAN_KINDS = (rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD, rtc.ParticipantKind.PARTICIPANT_KIND_SIP)


def _is_human(p: rtc.Participant) -> bool:
    return p.kind in HUMAN_KINDS


# --------------------------------------------------------------------------------------------------------------------------
# worker-level hooks (module level so spawned job processes can import them by reference)
# --------------------------------------------------------------------------------------------------------------------------


def prewarm(proc: JobProcess) -> None:
    """Runs once per job process: silero VAD + the Moonshine model (the TTS engine is a remote sidecar)."""
    settings = get_settings()
    proc.userdata["vad"] = silero.VAD.load()
    proc.userdata["transcriber"] = load_transcriber(settings.moonshine_language, settings.moonshine_arch)


def identity_from_metadata(raw: str) -> str | None:
    """`{"identity_id": "..."}` -> the id; None when the metadata is missing or malformed."""
    try:
        value = json.loads(raw) if raw else None
    except ValueError:
        return None
    if isinstance(value, dict) and isinstance(value.get("identity_id"), str) and value["identity_id"]:
        return value["identity_id"]
    return None


def load_resolved(settings: Settings, identity_id: str) -> ResolvedIdentity:
    conn = connect(settings.db_path)
    try:
        return get_identity(conn, identity_id)
    finally:
        conn.close()


async def request_fnc(req: JobRequest) -> None:
    """Accept only dispatches that name a known Identity and target exactly its Default Room."""
    settings = get_settings()
    identity_id = identity_from_metadata(req.job.metadata)
    if identity_id is None:
        log.warning("rejecting job %s: dispatch metadata has no identity_id", req.id)
        await req.reject()
        return
    try:
        resolved = await asyncio.to_thread(load_resolved, settings, identity_id)
    except DomainError as exc:
        log.warning("rejecting job %s: %s", req.id, exc.message)
        await req.reject()
        return
    if req.room.name != resolved.participant.default_room:
        log.warning("rejecting job %s: room %r is not the default room of %s", req.id, req.room.name, identity_id)
        await req.reject()
        return
    await req.accept(identity=resolved.participant.participant_identity, name=resolved.self_.name)


class IdentityAgent(Agent):
    """Agent whose `llm_node` reports every LLM output chunk to the latency recorder (T2) without altering it."""

    def __init__(self, *, instructions: str, recorder: LatencyRecorder, on_llm_ok: Any) -> None:
        super().__init__(instructions=instructions)
        self._recorder = recorder
        self._on_llm_ok = on_llm_ok

    async def llm_node(
        self, chat_ctx: llm.ChatContext, tools: list[llm.Tool], model_settings: ModelSettings
    ) -> AsyncIterable[llm.ChatChunk | str | Any]:
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            if isinstance(chunk, str):
                text = chunk
            elif isinstance(chunk, llm.ChatChunk) and chunk.delta is not None:
                text = chunk.delta.content or ""
            else:
                text = ""
            self._recorder.on_llm_chunk(text)
            if text:
                self._on_llm_ok()
            yield chunk


def build_session(settings: Settings, resolved: ResolvedIdentity, proc: JobProcess, recorder: LatencyRecorder) -> AgentSession:
    profile_id = resolved.voice.profile_id
    assert profile_id is not None  # entrypoint refuses voiceless identities first
    effort: Any = settings.llm_reasoning_effort or NOT_GIVEN
    chatterbox = ChatterboxTTS(
        base_url=settings.voice_engine_url,
        profile_id=profile_id,
        read_timeout_s=settings.voice_engine_timeout_s,
        on_first_audio=recorder.on_tts_first_audio,
    )
    return AgentSession(
        stt=MoonshineSTT(proc.userdata["transcriber"], language=resolved.self_.language.split("-")[0]),
        vad=proc.userdata["vad"],
        llm=openai.LLM(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            reasoning_effort=effort,
            max_completion_tokens=300,
        ),
        tts=chatterbox,
        turn_handling={
            "interruption": {"enabled": resolved.conversation.barge_in, "mode": "vad", "min_duration": 0.3},
            "endpointing": {"min_delay": 0.3},
            "preemptive_generation": {"enabled": True},
        },
    )


def _component(source: Any) -> tuple[str, str] | None:
    """(status_detail, label) for the failing component of an ErrorEvent source."""
    if isinstance(source, llm.LLM):
        return P.LLM_UNAVAILABLE, "llm"
    if isinstance(source, stt.STT):
        return P.STT_FAILURE, "stt"
    if isinstance(source, tts.TTS):
        return P.TTS_UNAVAILABLE, "tts"
    return None


async def entrypoint(ctx: JobContext) -> None:
    settings = get_settings()
    identity_id = identity_from_metadata(ctx.job.metadata)
    if identity_id is None:  # request_fnc already rejects these; defend anyway
        raise RuntimeError("job has no identity_id metadata")

    conn = connect(settings.db_path)
    session_id = uuid.uuid4().hex
    pres = P.PresenceController(conn, identity_id)
    recorder = LatencyRecorder(lambda: connect(settings.db_path), identity_id, session_id)
    state: dict[str, Any] = {"session": None, "ended": False}

    try:
        pres.claim()
    except P.Superseded as exc:
        log.info("job for %s not started: %s", identity_id, exc)
        conn.close()
        ctx.shutdown("superseded")
        return

    resolved = load_resolved(settings, identity_id)  # FRESH from the database for every job
    conn.execute(
        "INSERT INTO runtime_sessions (session_id, identity_id, room, started_at) VALUES (?, ?, ?, ?)",
        (session_id, identity_id, resolved.participant.default_room, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    async def finish(reason: str) -> None:
        if state["ended"]:
            return
        state["ended"] = True
        recorder.close()
        try:
            conn.execute(
                "UPDATE runtime_sessions SET ended_at = ?, end_reason = ? WHERE session_id = ?",
                (datetime.now(timezone.utc).isoformat(), reason, session_id),
            )
            conn.commit()
            pres.release()
        finally:
            conn.close()

    ctx.add_shutdown_callback(finish)

    def guarded(fn):
        """Event handlers run on the session loop: a superseded job shuts itself down, anything else is logged loudly."""

        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except P.Superseded as exc:
                log.info("%s: %s; shutting down", identity_id, exc)
                ctx.shutdown("superseded")
            except Exception:
                log.exception("runtime handler %s failed for %s", getattr(fn, "__name__", fn), identity_id)

        return wrapper

    def fail_job(detail: str, message: str) -> None:
        log.error("%s: %s (%s)", identity_id, message, detail)
        pres.fail(detail, message)

    if resolved.voice.profile_id is None:
        fail_job(P.VOICE_REQUIRED, "identity has no voice profile; refusing to run with a default voice")
        return  # job stays registered in ERROR until stopped; nothing is published or spoken

    try:
        await ctx.connect()
        engine = VoiceEngineClient(settings.voice_engine_url, settings.voice_engine_timeout_s)
        try:
            await engine.warm(resolved.voice.profile_id)
        except DomainError as exc:
            fail_job(P.TTS_UNAVAILABLE, f"voice engine could not warm the voice profile: {exc.message}")
            return

        agent = IdentityAgent(
            instructions=build_instructions(resolved), recorder=recorder, on_llm_ok=guarded(lambda: pres.recovered(P.LLM_UNAVAILABLE))
        )
        session = build_session(settings, resolved, ctx.proc, recorder)
        state["session"] = session

        session.on("user_state_changed", guarded(lambda ev: recorder.on_user_state(ev.old_state, ev.new_state)))
        session.on("user_input_transcribed", guarded(lambda ev: recorder.on_user_transcript(ev.transcript, ev.is_final)))
        session.on("agent_state_changed", guarded(lambda ev: recorder.on_agent_state(ev.old_state, ev.new_state)))

        def on_error(ev: ErrorEvent) -> None:
            comp = _component(ev.source)
            msg = f"{type(ev.error).__name__}: {getattr(ev.error, 'message', ev.error)}"[:500]
            if comp is None:
                log.error("%s: session error from %r: %s", identity_id, ev.source, msg)
                return
            fail_job(comp[0], f"{comp[1]} error: {msg}")

        def on_close(ev: CloseEvent) -> None:
            log.warning("%s: session closed: %s", identity_id, ev.reason)
            if ev.error is not None and pres.error_detail is None:
                fail_job(P.LLM_UNAVAILABLE, f"session closed after an unrecoverable error: {ev.error}")

        session.on("error", guarded(on_error))
        session.on("close", guarded(on_close))

        room = ctx.room
        own = room.local_participant.identity

        def on_join(p: rtc.RemoteParticipant) -> None:
            if _is_human(p):
                pres.human_joined(p.identity)

        def on_leave(p: rtc.RemoteParticipant) -> None:
            if _is_human(p):
                pres.human_left(p.identity)

        room.on("participant_connected", guarded(on_join))
        room.on("participant_disconnected", guarded(on_leave))
        room.on("reconnecting", guarded(lambda: pres.set_reconnecting(True)))
        room.on("reconnected", guarded(lambda: pres.set_reconnecting(False)))
        room.on("disconnected", guarded(lambda reason=None: log.warning("%s: room disconnected: %s", identity_id, reason)))

        await session.start(agent, room=room, room_options=room_io.RoomOptions(close_on_disconnect=False))
        for p in room.remote_participants.values():
            if p.identity != own and _is_human(p):
                pres._humans.add(p.identity)
        session.say(resolved.conversation.greeting)  # TTS only; no LLM involved
        pres.refresh()  # STARTING -> READY (BUSY when a human is already in the room)
    except P.Superseded as exc:
        log.info("%s: %s; shutting down", identity_id, exc)
        ctx.shutdown("superseded")
        return
    except Exception as exc:
        log.exception("job for %s failed during startup", identity_id)
        try:
            pres.fail(P.TTS_UNAVAILABLE if "engine" in str(exc).lower() else "startup_failed", f"{type(exc).__name__}: {exc}"[:500])
        except P.Superseded:
            ctx.shutdown("superseded")
        return

    async def beat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            try:
                pres.heartbeat()
            except P.Superseded as exc:
                log.info("%s: %s; shutting down", identity_id, exc)
                ctx.shutdown("superseded")
                return
            except Exception:
                log.exception("heartbeat failed for %s", identity_id)

    hb = asyncio.create_task(beat(), name=f"heartbeat-{identity_id}")
    ctx.add_shutdown_callback(lambda: _cancel(hb))


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def build_server(settings: Settings) -> AgentServer:
    os.environ["LIVEKIT_AGENT_NAME"] = settings.livekit_agent_name
    server = AgentServer(
        ws_url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        # Windows defaults to threads; Moonshine/VAD state must be per-process, one job per process.
        job_executor_type=JobExecutorType.PROCESS,
        num_idle_processes=1,
        port=0,  # ephemeral health port: never collides with another worker or the API
        setup_fnc=prewarm,
        initialize_process_timeout=120.0,  # Moonshine model load on first run
    )
    server.rtc_session(entrypoint, on_request=request_fnc)
    return server


async def _serve(server: AgentServer) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop(*_: Any) -> None:
        loop.call_soon_threadsafe(stop.set)

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, request_stop)

    runner = asyncio.create_task(server.run(), name="agent-server")
    waiter = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait({runner, waiter}, return_when=asyncio.FIRST_COMPLETED)
    if runner in done:
        waiter.cancel()
        runner.result()  # surface a startup failure (e.g. LiveKit unreachable) as a non-zero exit
        return
    log.info("stop requested; closing worker")
    await server.aclose()
    runner.cancel()
    try:
        await runner
    except (asyncio.CancelledError, Exception):
        pass


def main() -> None:
    logging.basicConfig(level=os.environ.get("SERVICE015_RUNTIME_LOG", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = get_settings()
    migrate_conn = connect(settings.db_path)
    try:
        migrate(migrate_conn)
    finally:
        migrate_conn.close()
    asyncio.run(_serve(build_server(settings)))


if __name__ == "__main__":
    main()
    sys.exit(0)
