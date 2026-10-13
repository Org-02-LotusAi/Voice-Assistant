"""LiveKitSupervisor: launch / stop / status of Identity participants through LiveKit explicit dispatch.

launch = preflight -> ensure runtime worker -> ensure Default Room -> STARTING -> create dispatch (agent_name, room,
metadata={"identity_id"}). The job (runtime/worker.py) then owns the status row (pid) and writes READY/BUSY/ERROR itself.

Preflight failures raise 503 `dependency_unavailable` (`details.dependency` = livekit | llm | voice_engine | voicebox) BEFORE
any state is touched, so a failed launch leaves the Identity exactly as it was.

Staleness (reported, never written): heartbeat/start-timeout rules live in `participants.apply_staleness` so every reader
agrees; this supervisor adds `worker_exited` (STARTING/READY/BUSY while the worker it owns is gone). launch() treats such an
Identity as relaunchable. Process lifecycle (Job Object, pidfile adoption, tree kill) is in `runtime/procs.py`;
`startup()` adopts a live worker or, when none is alive, marks every orphaned row OFFLINE `runtime_lost`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from livekit import api

from service015 import identities_repo
from service015.config import Settings
from service015.db import connect
from service015.errors import Conflict, DependencyUnavailable, DomainError
from service015.participants import participant_status, roster
from service015.runtime.procs import WorkerProcess, allowed_env
from service015.voice.authz import check_voice_provider
from service015.voice.engine import VoiceEngineClient

log = logging.getLogger("service015.supervisor")

WORKER_READY_TIMEOUT_S = 60.0
LIVEKIT_PROBE_TIMEOUT_S = 5.0
LLM_PROBE_TIMEOUT_S = 10.0
DEEPGRAM_PROBE_URL = "https://api.deepgram.com/v1/auth/token"  # "Get Token Details": 200 for any valid key, whatever its scopes
DEEPGRAM_PROBE_TIMEOUT_S = 5.0
DEEPGRAM_PROBE_TTL_S = 300.0  # a successful probe is trusted this long so launches do not re-probe every time
ROOM_TIMEOUT_S = 24 * 3600  # keep the Default Room alive while only the agent is in it


def dependency_error(dependency: str, message: str, **extra: Any) -> DependencyUnavailable:
    return DependencyUnavailable(
        "dependency_unavailable", message, details={"dependency": dependency, **extra}
    )


def runtime_unavailable(message: str, **extra: Any) -> DependencyUnavailable:
    return DependencyUnavailable("runtime_unavailable", message, details=extra)


class LiveKitSupervisor:
    """Implements the RuntimeSupervisor protocol.

    `engine`, `llm_transport` and `api_factory` are injection points for tests (mocks); `worker` may be replaced by a stub to
    skip spawning. Production use passes only `settings`.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        engine: VoiceEngineClient | None = None,
        llm_transport: httpx.AsyncBaseTransport | None = None,
        deepgram_transport: httpx.AsyncBaseTransport | None = None,
        api_factory: Callable[[], api.LiveKitAPI] | None = None,
        worker: WorkerProcess | None = None,
        worker_ready_timeout_s: float = WORKER_READY_TIMEOUT_S,
    ) -> None:
        self._s = settings
        self._engine = engine or VoiceEngineClient(settings.voice_engine_url, settings.voice_engine_timeout_s)
        self._llm_transport = llm_transport
        self._deepgram_transport = deepgram_transport
        self._deepgram_ok_until = 0.0  # time.monotonic() until which the last successful key probe stays valid
        self._api_factory = api_factory or (
            lambda: api.LiveKitAPI(settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret)
        )
        data_dir = Path(settings.db_path).resolve().parent
        self._worker = worker if worker is not None else WorkerProcess(
            worker_env(settings), data_dir / "runtime-worker.log", data_dir / "runtime-worker.pid"
        )
        self._ready_timeout = worker_ready_timeout_s
        self._worker_lock = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}

    # -- helpers --------------------------------------------------------------------------------------------------------

    def _lock(self, identity_id: str) -> asyncio.Lock:
        return self._locks.setdefault(identity_id, asyncio.Lock())

    def _conn(self):
        return connect(self._s.db_path)

    def _status(self, identity_id: str) -> dict[str, Any]:
        conn = self._conn()
        try:
            st = participant_status(conn, identities_repo.get_identity(conn, identity_id))
            pid = identities_repo.get_status(conn, identity_id).pid
        finally:
            conn.close()
        return self._effective(st, pid)

    def _effective(self, st: dict[str, Any], pid: int | None) -> dict[str, Any]:
        """`participant_status` already applied the shared staleness rules; add `worker_exited` for the worker we own."""
        if st["presence"] in ("STARTING", "READY", "BUSY") and self._worker_spawned() and not self._worker.alive():
            return {**st, "presence": "ERROR", "status_detail": "worker_exited", "last_error": "the runtime worker process is gone"}
        return st

    def _worker_spawned(self) -> bool:
        return getattr(self._worker, "pid", None) is not None

    def _lk(self) -> api.LiveKitAPI:
        return self._api_factory()

    def _check_capacity(self) -> None:
        """409 `capacity_exceeded` once SERVICE015_MAX_ACTIVE_IDENTITIES identities are STARTING/READY/BUSY.

        Every running Identity is a process with Moonshine + VAD resident and shares one CPU-bound TTS engine, so the bound is
        a deterministic count (CPU-load shedding is disabled in the worker, see runtime/worker.py). Stale rows do not count.
        """
        conn = self._conn()
        try:
            rows = roster(conn)
        finally:
            conn.close()
        active = sum(1 for st in rows if self._effective(st, None)["presence"] in ("STARTING", "READY", "BUSY"))
        limit = self._s.max_active_identities
        if active >= limit:
            raise Conflict(
                "capacity_exceeded", f"{active} identities are already running (limit {limit}); stop one first",
                details={"active": active, "limit": limit},
            )

    # -- preflight ------------------------------------------------------------------------------------------------------

    async def preflight(self, voice_profile_id: str) -> None:
        """Voice matches the TTS provider, LiveKit reachable, LLM answers GET /models, and (local TTS only) the engine is healthy
        and the voice profile warmable. Deepgram mode needs neither the engine nor Voicebox."""
        local_tts = not check_voice_provider(voice_profile_id, self._s.tts_provider)  # 422 voice_provider_mismatch
        try:
            lk = self._lk()
            try:
                await asyncio.wait_for(lk.room.list_rooms(api.ListRoomsRequest()), LIVEKIT_PROBE_TIMEOUT_S)
            finally:
                await lk.aclose()
        except Exception as exc:  # aiohttp / twirp / timeout: all mean "LiveKit not usable"
            raise dependency_error("livekit", f"LiveKit is not reachable at {self._s.livekit_url}: {type(exc).__name__}") from None

        await self._probe_llm()
        if self._s.uses_deepgram:
            await self._probe_deepgram()

        if not local_tts:
            return
        health = await self._engine.health()
        if health != "ok":
            raise dependency_error("voice_engine", f"the voice engine is not healthy (status: {health})", engine_status=health)
        try:
            await self._engine.warm(voice_profile_id)
        except DependencyUnavailable as exc:
            dep = "voicebox" if exc.code == "voicebox_unavailable" else "voice_engine"
            raise dependency_error(dep, exc.message, upstream_code=exc.code) from None
        # other DomainErrors (profile unknown to the engine, reference_too_short, ...) are not dependency outages: pass through

    async def _probe_deepgram(self) -> None:
        """Authenticated GET against Deepgram: 401/403 -> `deepgram_auth_failed`, network error / other status ->
        `deepgram_unavailable` (both 503). A success is cached for DEEPGRAM_PROBE_TTL_S. The key never appears in an error."""
        if time.monotonic() < self._deepgram_ok_until:
            return
        try:
            async with httpx.AsyncClient(transport=self._deepgram_transport, timeout=DEEPGRAM_PROBE_TIMEOUT_S) as client:
                resp = await client.get(DEEPGRAM_PROBE_URL, headers={"Authorization": f"Token {self._s.deepgram_api_key}"})
        except httpx.HTTPError as exc:
            raise DependencyUnavailable(
                "deepgram_unavailable", f"Deepgram is unreachable: {type(exc).__name__}", details={"dependency": "deepgram"}
            ) from None
        if resp.status_code in (401, 403):
            raise DependencyUnavailable(
                "deepgram_auth_failed", f"Deepgram rejected the API key (HTTP {resp.status_code}); check DEEPGRAM_API_KEY",
                details={"dependency": "deepgram", "upstream_status": resp.status_code},
            )
        if resp.status_code != 200:
            raise DependencyUnavailable(
                "deepgram_unavailable", f"Deepgram answered HTTP {resp.status_code} to the key check",
                details={"dependency": "deepgram", "upstream_status": resp.status_code},
            )
        self._deepgram_ok_until = time.monotonic() + DEEPGRAM_PROBE_TTL_S

    async def _probe_llm(self) -> None:
        s = self._s
        if not s.llm_api_key:
            raise dependency_error("llm", "LLM_API_KEY is not configured")
        try:
            async with httpx.AsyncClient(transport=self._llm_transport, timeout=LLM_PROBE_TIMEOUT_S) as client:
                resp = await client.get(
                    s.llm_base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {s.llm_api_key}"}
                )
        except httpx.HTTPError as exc:
            raise dependency_error("llm", f"the LLM endpoint is unreachable: {type(exc).__name__}") from None
        if resp.status_code != 200:
            raise dependency_error("llm", f"the LLM endpoint answered HTTP {resp.status_code} to GET /models", upstream_status=resp.status_code)

    # -- worker ---------------------------------------------------------------------------------------------------------

    async def ensure_worker(self) -> None:
        """Spawn the runtime worker if not alive and wait (<= 60 s) until it registered with LiveKit."""
        async with self._worker_lock:
            if self._worker.alive() and self._worker.registered():
                return
            if not self._worker.alive():
                await asyncio.to_thread(self._worker.spawn)
            deadline = time.monotonic() + self._ready_timeout
            while time.monotonic() < deadline:
                if not self._worker.alive():
                    tail = self._worker.log_text()[-600:]
                    raise runtime_unavailable("the runtime worker exited during startup", worker_log_tail=tail)
                if self._worker.registered():
                    return
                await asyncio.sleep(0.5)
            await asyncio.to_thread(self._worker.stop)
            raise runtime_unavailable(
                f"the runtime worker did not register with LiveKit within {int(self._ready_timeout)} s"
            )

    # -- protocol -------------------------------------------------------------------------------------------------------

    async def launch(self, identity_id: str) -> dict[str, Any]:
        async with self._lock(identity_id):
            conn = self._conn()
            try:
                resolved = identities_repo.get_identity(conn, identity_id)
            finally:
                conn.close()
            current = self._status(identity_id)
            if current["presence"] in ("STARTING", "READY", "BUSY"):
                return current  # idempotent: already running
            self._check_capacity()
            profile = resolved.voice.profile_id
            if profile is None:
                raise DomainError("voice_required", "this identity has no voice", 422, {"identity_id": identity_id})
            await self.preflight(profile)  # raises before anything below changes state
            await self.ensure_worker()

            room = resolved.participant.default_room
            lk = self._lk()
            try:
                await lk.room.create_room(
                    api.CreateRoomRequest(name=room, empty_timeout=ROOM_TIMEOUT_S, departure_timeout=ROOM_TIMEOUT_S)
                )
                await self._cleanup(lk, room, resolved.participant.participant_identity)  # stale ERROR job / dispatches
                conn = self._conn()
                try:
                    identities_repo.set_status(conn, identity_id, "OFFLINE")  # drop any stale pid
                    identities_repo.set_status(conn, identity_id, "STARTING")
                finally:
                    conn.close()
                try:
                    await lk.agent_dispatch.create_dispatch(
                        api.CreateAgentDispatchRequest(
                            agent_name=self._s.livekit_agent_name, room=room, metadata=_metadata(identity_id)
                        )
                    )
                except Exception as exc:
                    conn = self._conn()
                    try:
                        identities_repo.set_status(conn, identity_id, "OFFLINE")
                    finally:
                        conn.close()
                    raise dependency_error("livekit", f"could not create the agent dispatch: {type(exc).__name__}") from None
            except DomainError:
                raise
            except Exception as exc:
                raise dependency_error("livekit", f"LiveKit request failed: {type(exc).__name__}") from None
            finally:
                await lk.aclose()
            return self._status(identity_id)

    async def _cleanup(self, lk: api.LiveKitAPI, room: str, participant_identity: str) -> None:
        """Delete this agent's dispatches in the room and remove the agent participant if it is present."""
        for d in await lk.agent_dispatch.list_dispatch(room):
            if d.agent_name == self._s.livekit_agent_name:
                await lk.agent_dispatch.delete_dispatch(d.id, room)
        try:
            await lk.room.remove_participant(api.RoomParticipantIdentity(room=room, identity=participant_identity))
        except api.TwirpError as exc:
            if exc.code != "not_found":
                raise

    async def stop(self, identity_id: str) -> dict[str, Any]:
        async with self._lock(identity_id):
            conn = self._conn()
            try:
                resolved = identities_repo.get_identity(conn, identity_id)
                # OFFLINE first: a job that outlives a failed LiveKit cleanup sees it no longer owns the row and exits itself
                identities_repo.set_status(conn, identity_id, "OFFLINE")
            finally:
                conn.close()
            failure: str | None = None
            lk = self._lk()
            try:
                await self._cleanup(lk, resolved.participant.default_room, resolved.participant.participant_identity)
            except Exception as exc:
                failure = type(exc).__name__
                log.exception("LiveKit cleanup failed while stopping %s", identity_id)
            finally:
                await lk.aclose()
            if failure is not None:
                raise dependency_error(
                    "livekit",
                    "LiveKit cleanup failed; the identity is marked OFFLINE and its job will exit at its next heartbeat",
                    error=failure,
                )
            return self._status(identity_id)

    async def status(self, identity_id: str) -> dict[str, Any]:
        return self._status(identity_id)

    async def startup(self) -> None:
        """API start: adopt a live worker from the pidfile; otherwise every STARTING/READY/BUSY row is orphaned -> OFFLINE."""
        adopted = await asyncio.to_thread(self._worker.adopt_existing)
        if adopted:
            return
        if not getattr(self._worker, "had_pidfile", True):
            return  # this API never managed a worker here: rows may belong to a worker started by hand, leave them
        conn = self._conn()
        try:
            n = reconcile_runtime_lost(conn)
        finally:
            conn.close()
        if n:
            log.warning("startup reconcile: %d identity row(s) marked OFFLINE runtime_lost", n)

    async def aclose(self) -> None:
        """API shutdown: drain and stop the worker (its jobs run their finish hooks), then close out anything left."""
        owned = self._worker.pid is not None
        await asyncio.to_thread(self._worker.stop)
        if owned:  # a worker started by hand is not ours to declare dead
            conn = self._conn()
            try:
                reconcile_runtime_lost(conn)
            finally:
                conn.close()


def _metadata(identity_id: str) -> str:
    return json.dumps({"identity_id": identity_id}, separators=(",", ":"))


def worker_env(settings: Settings) -> dict[str, str]:
    """Environment for the worker and its jobs: an allow-list of the parent env plus explicit settings (no API keys)."""
    env = allowed_env(dict(os.environ))
    env.update(
        SERVICE015_DB_PATH=str(Path(settings.db_path).resolve()),
        LIVEKIT_URL=settings.livekit_url,
        LIVEKIT_API_KEY=settings.livekit_api_key,
        LIVEKIT_API_SECRET=settings.livekit_api_secret,
        LIVEKIT_AGENT_NAME=settings.livekit_agent_name,
        LLM_BASE_URL=settings.llm_base_url,
        LLM_API_KEY=settings.llm_api_key,
        LLM_MODEL=settings.llm_model,
        LLM_REASONING_EFFORT=settings.llm_reasoning_effort,
        VOICE_ENGINE_URL=settings.voice_engine_url,
        VOICE_ENGINE_TIMEOUT_S=str(settings.voice_engine_timeout_s),
        VOICEBOX_URL=settings.voicebox_url,
        SERVICE015_STT_PROVIDER=settings.stt_provider,
        SERVICE015_TTS_PROVIDER=settings.tts_provider,
        SERVICE015_DEEPGRAM_STT_MODEL=settings.deepgram_stt_model,
        SERVICE015_DEEPGRAM_STT_LANGUAGE=settings.deepgram_stt_language,
        MOONSHINE_ARCH=settings.moonshine_arch,
        MOONSHINE_LANGUAGE=settings.moonshine_language,
        PYTHONUNBUFFERED="1",
    )
    if settings.uses_deepgram:
        env["DEEPGRAM_API_KEY"] = settings.deepgram_api_key  # only handed to the worker when a provider needs it
    return env


def reconcile_runtime_lost(conn) -> int:
    """No worker is alive: STARTING/READY/BUSY rows become OFFLINE `runtime_lost`, open runtime_sessions are ended.

    Returns the number of identity rows changed. ERROR rows are left as they are (they already name a fault).
    """
    rows = conn.execute("SELECT identity_id FROM runtime_status WHERE presence IN ('STARTING','READY','BUSY')").fetchall()
    for r in rows:
        identities_repo.set_status(conn, r["identity_id"], "OFFLINE", "runtime_lost")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE runtime_sessions SET ended_at = ?, end_reason = 'runtime_lost' WHERE ended_at IS NULL", (now,))
    conn.commit()
    return len(rows)
