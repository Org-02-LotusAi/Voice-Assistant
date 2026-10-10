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
import math
import multiprocessing
import os
import signal
import sys
import time
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

if multiprocessing.current_process().name == "job_proc" and hasattr(signal, "SIGBREAK"):
    # CTRL_BREAK is delivered to the whole console process group, job children included. The SDK already ignores SIGINT and
    # SIGTERM in jobs; ignoring SIGBREAK too lets the worker drain its jobs so every job's `finish` hook runs.
    signal.signal(signal.SIGBREAK, signal.SIG_IGN)

HEARTBEAT_S = 5.0
MAX_EMPTY_LLM_ATTEMPTS = 3  # total LLM requests per generation when the provider returns an empty completion
GREETING_DELAY_S = 0.8
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
    """Agent whose `llm_node` reports each generation's chunks to the latency recorder (T2, agent text) without altering them."""

    def __init__(self, *, instructions: str, recorder: LatencyRecorder) -> None:
        super().__init__(instructions=instructions)
        self._recorder = recorder
        self.chunks_seen = 0  # LLM output chunks produced so far (evidence that the LLM path works)

    async def llm_node(
        self, chat_ctx: llm.ChatContext, tools: list[llm.Tool], model_settings: ModelSettings
    ) -> AsyncIterable[llm.ChatChunk | str | Any]:
        gen = self._recorder.begin_generation()
        t_start = time.monotonic()
        # An LLM completion that carries no text and no tool call would end the speech silently and lose the user's turn (the
        # provider occasionally answers 200 OK with an empty stream). Nothing has been yielded in that case, so the request is
        # simply repeated; the reply is still LLM-generated, never invented here.
        for attempt in range(1, MAX_EMPTY_LLM_ATTEMPTS + 1):
            log.info("llm_node start (measured=%s, items=%d, attempt=%d)", gen is not None, len(chat_ctx.items), attempt)
            n_chunks, produced, raw = 0, False, []
            try:
                async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
                    n_chunks += 1
                    if n_chunks == 1:
                        log.info("llm_node first chunk after %.2fs", time.monotonic() - t_start)
                    text = _chunk_text(chunk)
                    if text or _chunk_has_tool_call(chunk):
                        produced = True
                    elif len(raw) < 5:
                        raw.append(repr(chunk)[:300])
                    self._recorder.on_llm_chunk(gen, text)
                    if text:
                        self.chunks_seen += 1
                    yield chunk
            except asyncio.CancelledError:
                log.info("llm_node CANCELLED after %.2fs (%d chunks)", time.monotonic() - t_start, n_chunks)
                raise
            log.info("llm_node end after %.2fs (%d chunks, produced=%s)", time.monotonic() - t_start, n_chunks, produced)
            if produced:
                return
            log.warning("llm_node produced no text and no tool call (attempt %d/%d); chunks: %s", attempt, MAX_EMPTY_LLM_ATTEMPTS, raw)
        log.error("llm_node: %d consecutive empty LLM completions; this user turn gets no reply", MAX_EMPTY_LLM_ATTEMPTS)


def _chunk_text(chunk: Any) -> str:
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, llm.ChatChunk) and chunk.delta is not None:
        return chunk.delta.content or ""
    return ""


def _chunk_has_tool_call(chunk: Any) -> bool:
    return isinstance(chunk, llm.ChatChunk) and chunk.delta is not None and bool(chunk.delta.tool_calls)


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


_ERROR_TYPE_DETAIL = {"llm_error": P.LLM_UNAVAILABLE, "stt_error": P.STT_FAILURE, "tts_error": P.TTS_UNAVAILABLE}
_DETAIL_LABEL = {P.LLM_UNAVAILABLE: "llm", P.STT_FAILURE: "stt", P.TTS_UNAVAILABLE: "tts"}


def classify_error(error: Any, source: Any = None) -> tuple[str, str] | None:
    """(status_detail, component label) from an SDK error model (`error.type`) or, failing that, the failing component."""
    detail = _ERROR_TYPE_DETAIL.get(getattr(error, "type", ""))
    if detail is None:
        if isinstance(source, llm.LLM):
            detail = P.LLM_UNAVAILABLE
        elif isinstance(source, stt.STT):
            detail = P.STT_FAILURE
        elif isinstance(source, tts.TTS):
            detail = P.TTS_UNAVAILABLE
    return (detail, _DETAIL_LABEL[detail]) if detail else None


async def entrypoint(ctx: JobContext) -> None:
    settings = get_settings()
    identity_id = identity_from_metadata(ctx.job.metadata)
    if identity_id is None:  # request_fnc already rejects these; defend anyway
        ctx.shutdown("no identity_id in dispatch metadata")
        raise RuntimeError("job has no identity_id metadata")

    loop = asyncio.get_running_loop()
    conn = connect(settings.db_path)  # used ONLY from the writer thread (check_same_thread=False, one thread at a time)
    writer = P.JobWriter(loop, lambda: ctx.shutdown("superseded"), identity_id)
    session_id = uuid.uuid4().hex
    pres = P.PresenceController(conn, identity_id)
    recorder = LatencyRecorder(lambda: connect(settings.db_path), identity_id, session_id, submit=writer.submit)
    state: dict[str, Any] = {"ended": False}
    humans: set[str] = set()
    tasks: set[asyncio.Task] = set()

    def spawn(coro) -> None:
        t = asyncio.create_task(coro)
        tasks.add(t)

        def done(task: asyncio.Task) -> None:
            tasks.discard(task)
            if not task.cancelled() and task.exception() is not None:
                log.error("background task failed for %s", identity_id, exc_info=task.exception())

        t.add_done_callback(done)

    async def finish(reason: str) -> None:
        if state["ended"]:
            return
        state["ended"] = True
        for t in list(tasks):
            t.cancel()
        recorder.close()  # queues the last turn write on the same ordered writer

        def end() -> str | None:
            conn.execute(
                "UPDATE runtime_sessions SET ended_at = ?, end_reason = ? WHERE session_id = ?",
                (datetime.now(timezone.utc).isoformat(), reason, session_id),
            )
            conn.commit()
            return pres.end(reason)

        try:
            await writer.run(end)
        except Exception:
            log.exception("job end bookkeeping failed for %s", identity_id)
        finally:
            await asyncio.to_thread(writer.close)
            conn.close()

    ctx.add_shutdown_callback(finish)

    def bg(fn, *args) -> None:
        writer.submit(fn, *args)

    def fail_job(detail: str, message: str, terminal: bool = False) -> None:
        log.error("%s: %s (%s)", identity_id, message, detail)
        bg(pres.fail, detail, message, terminal)

    try:
        await writer.run(pres.claim)
    except P.Superseded as exc:
        log.info("job for %s not started: %s", identity_id, exc)
        ctx.shutdown("superseded")
        return

    resolved = await asyncio.to_thread(load_resolved, settings, identity_id)  # FRESH from the database for every job

    def open_session() -> None:
        conn.execute(
            "INSERT INTO runtime_sessions (session_id, identity_id, room, started_at) VALUES (?, ?, ?, ?)",
            (session_id, identity_id, resolved.participant.default_room, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

    await writer.run(open_session)

    if resolved.voice.profile_id is None:
        fail_job(P.VOICE_REQUIRED, "identity has no voice profile; refusing to run with a default voice", terminal=True)
        ctx.shutdown("voice_required")  # nothing is published or spoken; the ERROR row stays visible
        return

    try:
        await ctx.connect()
        engine = VoiceEngineClient(settings.voice_engine_url, settings.voice_engine_timeout_s)
        try:
            await engine.warm(resolved.voice.profile_id)  # conditionals cached now, so the greeting is fast
        except DomainError as exc:
            fail_job(P.TTS_UNAVAILABLE, f"voice engine could not warm the voice profile: {exc.message}", terminal=True)
            ctx.shutdown("tts_unavailable")
            return

        agent = IdentityAgent(instructions=build_instructions(resolved), recorder=recorder)
        llm_failed_at = [0]  # agent.chunks_seen when the LLM last failed
        session = build_session(settings, resolved, ctx.proc, recorder)

        def guarded(fn):
            def wrapper(*a, **kw):
                try:
                    return fn(*a, **kw)
                except Exception:
                    log.exception("runtime handler %s failed for %s", getattr(fn, "__name__", fn), identity_id)

            return wrapper

        def on_user_state(ev: UserStateChangedEvent) -> None:
            log.info("%s: user state %s -> %s", identity_id, ev.old_state, ev.new_state)
            recorder.on_user_state(ev.old_state, ev.new_state)

        def on_agent_state(ev: AgentStateChangedEvent) -> None:
            log.info("%s: agent state %s -> %s", identity_id, ev.old_state, ev.new_state)
            recorder.on_agent_state(ev.old_state, ev.new_state)
            if ev.old_state == "speaking" and ev.new_state != "speaking":
                # Speech that played proves the TTS path; it proves the LLM only if the LLM produced output since it failed.
                bg(pres.recovered, P.TTS_UNAVAILABLE)
                if agent.chunks_seen > llm_failed_at[0]:
                    bg(pres.recovered, P.LLM_UNAVAILABLE)

        session.on("user_state_changed", guarded(on_user_state))
        def on_transcript(ev) -> None:
            log.info("%s: transcript %s: %r", identity_id, "FINAL" if ev.is_final else "interim", ev.transcript)
            recorder.on_user_transcript(ev.transcript, ev.is_final)
            if ev.is_final:
                bg(pres.recovered, P.STT_FAILURE)  # a final transcript proves the STT path

        session.on("user_input_transcribed", guarded(on_transcript))
        session.on("agent_state_changed", guarded(on_agent_state))

        def on_speech_created(ev) -> None:
            h = ev.speech_handle
            log.info("%s: speech created id=%s source=%s user_initiated=%s", identity_id, h.id, ev.source, ev.user_initiated)
            h.add_done_callback(
                lambda hh: log.info("%s: speech done id=%s interrupted=%s", identity_id, hh.id, hh.interrupted)
            )

        session.on("speech_created", guarded(on_speech_created))
        session.on(
            "agent_false_interruption",
            guarded(lambda ev: log.info("%s: false interruption resumed=%s", identity_id, ev.resumed)),
        )
        session.on(
            "user_transcription_timeout",
            guarded(lambda ev: log.info("%s: user_transcription_timeout %r", identity_id, ev)),
        )

        def on_error(ev: ErrorEvent) -> None:
            comp = classify_error(ev.error, ev.source)
            msg = f"{type(ev.error).__name__}: {getattr(ev.error, 'message', ev.error)}"[:500]
            if comp is None:
                log.error("%s: session error from %r: %s", identity_id, ev.source, msg)
                return
            if getattr(ev.error, "recoverable", False):
                log.warning("%s: recoverable %s error (retrying): %s", identity_id, comp[1], msg)
                bg(pres.note_error, f"{comp[1]} (recovered): {msg}")
                return
            if comp[0] == P.LLM_UNAVAILABLE:
                llm_failed_at[0] = agent.chunks_seen
            fail_job(comp[0], f"{comp[1]} error: {msg}")

        def on_close(ev: CloseEvent) -> None:
            log.warning("%s: session closed: %s", identity_id, ev.reason)
            if ev.error is not None:
                comp = classify_error(ev.error) or ("session_error", "session")
                fail_job(comp[0], f"session closed after an unrecoverable {comp[1]} error")

        session.on("error", guarded(on_error))
        session.on("close", guarded(on_close))

        room = ctx.room
        own = room.local_participant.identity
        started = asyncio.Event()
        greeted = [False]

        async def greet() -> None:
            await started.wait()  # a human may join while session.start is still running
            await asyncio.sleep(GREETING_DELAY_S)  # let the new participant finish subscribing to the agent's audio
            if humans and not greeted[0]:
                greeted[0] = True  # once per occupancy (reset when the room empties): a reload greets again, nothing greets twice
                session.say(resolved.conversation.greeting)  # TTS only; no LLM involved

        def on_join(p: rtc.RemoteParticipant) -> None:
            if not _is_human(p):
                return
            first = not humans
            humans.add(p.identity)
            bg(pres.set_humans, frozenset(humans))
            if first:
                spawn(greet())  # the greeting is spoken when someone is there to hear it

        def on_leave(p: rtc.RemoteParticipant) -> None:
            if _is_human(p):
                humans.discard(p.identity)
                if not humans:
                    greeted[0] = False
                bg(pres.set_humans, frozenset(humans))

        room.on("participant_connected", guarded(on_join))
        room.on("participant_disconnected", guarded(on_leave))
        room.on("reconnecting", guarded(lambda: bg(pres.set_reconnecting, True)))
        room.on("reconnected", guarded(lambda: bg(pres.set_reconnecting, False)))
        room.on("disconnected", guarded(lambda reason=None: log.warning("%s: room disconnected: %s", identity_id, reason)))

        await session.start(agent, room=room, room_options=room_io.RoomOptions(close_on_disconnect=False))
        humans.update(p.identity for p in room.remote_participants.values() if p.identity != own and _is_human(p))
        started.set()
        await writer.run(pres.set_humans, frozenset(humans))  # STARTING -> READY (BUSY when a human is already in the room)
        if humans:
            spawn(greet())
    except P.Superseded as exc:
        log.info("%s: %s; shutting down", identity_id, exc)
        ctx.shutdown("superseded")
        return
    except Exception as exc:
        log.exception("job for %s failed during startup", identity_id)
        fail_job("startup_failed", f"{type(exc).__name__}: {exc}"[:500], terminal=True)
        ctx.shutdown("startup_failed")
        return

    async def beat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            try:
                await writer.run(pres.heartbeat)
            except P.Superseded as exc:
                log.info("%s: %s; shutting down", identity_id, exc)
                ctx.shutdown("superseded")
                return
            except Exception:
                log.exception("heartbeat failed for %s", identity_id)

    spawn(beat())


def build_server(settings: Settings) -> AgentServer:
    os.environ["LIVEKIT_AGENT_NAME"] = settings.livekit_agent_name
    server = AgentServer(
        ws_url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        # Windows defaults to threads; Moonshine/VAD state must be per-process, one job per process.
        job_executor_type=JobExecutorType.PROCESS,
        num_idle_processes=1,
        # CPU-load shedding is off (SDK switch: an infinite threshold makes `_is_available` always True). The TTS engine and
        # Moonshine load saturate this host, which starved a relaunch dispatch (no job request ever arrived). A finite
        # threshold also declines a second back-to-back dispatch (idle estimate = threshold / idle processes). The
        # bound on running Identities is SERVICE015_MAX_ACTIVE_IDENTITIES, enforced by the supervisor.
        load_threshold=math.inf,
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
