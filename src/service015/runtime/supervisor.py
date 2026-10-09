"""LiveKitSupervisor: launch / stop / status of Identity participants through LiveKit explicit dispatch.

launch = preflight -> ensure runtime worker -> ensure Default Room -> STARTING -> create dispatch (agent_name, room,
metadata={"identity_id"}). The job (runtime/worker.py) then owns the status row (pid) and writes READY/BUSY/ERROR itself.

Preflight failures raise 503 `dependency_unavailable` (`details.dependency` = livekit | llm | voice_engine | voicebox) BEFORE
any state is touched, so a failed launch leaves the Identity exactly as it was.

Staleness (status() reports, it never writes): READY/BUSY with a heartbeat older than 15 s -> ERROR `heartbeat_lost`;
any of STARTING/READY/BUSY while the worker this supervisor spawned has exited -> ERROR `worker_exited`;
STARTING for longer than 120 s -> ERROR `start_timeout`. launch() treats such an Identity as relaunchable (it cleans up first).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
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
from service015.errors import DependencyUnavailable, DomainError
from service015.participants import participant_status
from service015.voice.engine import VoiceEngineClient

log = logging.getLogger("service015.supervisor")

HEARTBEAT_STALE_S = 15.0
STARTING_STALE_S = 120.0
WORKER_READY_TIMEOUT_S = 60.0
WORKER_STOP_TIMEOUT_S = 15.0
WORKER_READY_MARKER = "registered worker"
LIVEKIT_PROBE_TIMEOUT_S = 5.0
LLM_PROBE_TIMEOUT_S = 10.0
ROOM_TIMEOUT_S = 24 * 3600  # keep the Default Room alive while only the agent is in it


def dependency_error(dependency: str, message: str, **extra: Any) -> DependencyUnavailable:
    return DependencyUnavailable(
        "dependency_unavailable", message, details={"dependency": dependency, **extra}
    )


def runtime_unavailable(message: str, **extra: Any) -> DependencyUnavailable:
    return DependencyUnavailable("runtime_unavailable", message, details=extra)


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    return datetime.fromisoformat(value).timestamp()


class WorkerProcess:
    """The `service015-runtime` subprocess this supervisor spawned (module form: python -m service015.runtime.worker)."""

    def __init__(self, settings: Settings, log_path: Path) -> None:
        self._settings = settings
        self._log_path = log_path
        self._proc: subprocess.Popen | None = None
        self._log_file: Any = None

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    def _env(self) -> dict[str, str]:
        s = self._settings
        env = dict(os.environ)
        env.update(
            SERVICE015_DB_PATH=str(Path(s.db_path).resolve()),
            LIVEKIT_URL=s.livekit_url,
            LIVEKIT_API_KEY=s.livekit_api_key,
            LIVEKIT_API_SECRET=s.livekit_api_secret,
            LIVEKIT_AGENT_NAME=s.livekit_agent_name,
            LLM_BASE_URL=s.llm_base_url,
            LLM_API_KEY=s.llm_api_key,
            LLM_MODEL=s.llm_model,
            LLM_REASONING_EFFORT=s.llm_reasoning_effort,
            VOICE_ENGINE_URL=s.voice_engine_url,
            VOICE_ENGINE_TIMEOUT_S=str(s.voice_engine_timeout_s),
            MOONSHINE_ARCH=s.moonshine_arch,
            MOONSHINE_LANGUAGE=s.moonshine_language,
            PYTHONUNBUFFERED="1",
        )
        return env

    def spawn(self) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(self._log_path, "wb")  # truncated per spawn: the ready marker must come from THIS process
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "service015.runtime.worker"],
            env=self._env(),
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
        )
        log.info("spawned runtime worker pid=%s log=%s", self._proc.pid, self._log_path)

    def log_text(self) -> str:
        try:
            return self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def registered(self) -> bool:
        return WORKER_READY_MARKER in self.log_text()

    def stop(self) -> None:
        """Ask the worker to drain (CTRL_BREAK / SIGTERM); kill its whole process tree if it does not exit in time."""
        proc = self._proc
        try:
            if proc is not None and proc.poll() is None:
                try:
                    proc.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGTERM)
                    proc.wait(timeout=WORKER_STOP_TIMEOUT_S)
                except (subprocess.TimeoutExpired, OSError):
                    self._kill_tree(proc)
        finally:
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None
            self._proc = None

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            proc.kill()
        proc.wait(timeout=10)


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
        api_factory: Callable[[], api.LiveKitAPI] | None = None,
        worker: WorkerProcess | None = None,
        worker_ready_timeout_s: float = WORKER_READY_TIMEOUT_S,
    ) -> None:
        self._s = settings
        self._engine = engine or VoiceEngineClient(settings.voice_engine_url, settings.voice_engine_timeout_s)
        self._llm_transport = llm_transport
        self._api_factory = api_factory or (
            lambda: api.LiveKitAPI(settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret)
        )
        log_path = Path(settings.db_path).resolve().parent / "runtime-worker.log"
        self._worker = worker if worker is not None else WorkerProcess(settings, log_path)
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
        """Apply the staleness rules to a stored status (returned as a copy; nothing is written)."""
        presence = st["presence"]
        if presence not in ("STARTING", "READY", "BUSY"):
            return st
        now = time.time()
        reason: tuple[str, str] | None = None
        if self._worker_spawned() and not self._worker.alive():
            reason = ("worker_exited", "the runtime worker process is gone")
        elif presence in ("READY", "BUSY"):
            hb = _parse_ts(st["heartbeat_at"])
            if hb is None or now - hb > HEARTBEAT_STALE_S:
                reason = ("heartbeat_lost", f"no heartbeat for more than {int(HEARTBEAT_STALE_S)} s")
        else:
            launched = _parse_ts(st["launched_at"])
            if launched is not None and now - launched > STARTING_STALE_S:
                reason = ("start_timeout", f"still STARTING after {int(STARTING_STALE_S)} s")
        if reason is None:
            return st
        return {**st, "presence": "ERROR", "status_detail": reason[0], "last_error": reason[1]}

    def _worker_spawned(self) -> bool:
        return getattr(self._worker, "pid", None) is not None

    def _lk(self) -> api.LiveKitAPI:
        return self._api_factory()

    # -- preflight ------------------------------------------------------------------------------------------------------

    async def preflight(self, voice_profile_id: str) -> None:
        """LiveKit reachable, LLM answers GET /models, engine healthy and the voice profile warmable."""
        try:
            lk = self._lk()
            try:
                await asyncio.wait_for(lk.room.list_rooms(api.ListRoomsRequest()), LIVEKIT_PROBE_TIMEOUT_S)
            finally:
                await lk.aclose()
        except Exception as exc:  # aiohttp / twirp / timeout: all mean "LiveKit not usable"
            raise dependency_error("livekit", f"LiveKit is not reachable at {self._s.livekit_url}: {type(exc).__name__}") from None

        await self._probe_llm()

        health = await self._engine.health()
        if health != "ok":
            raise dependency_error("voice_engine", f"the voice engine is not healthy (status: {health})", engine_status=health)
        try:
            await self._engine.warm(voice_profile_id)
        except DependencyUnavailable as exc:
            dep = "voicebox" if exc.code == "voicebox_unavailable" else "voice_engine"
            raise dependency_error(dep, exc.message, upstream_code=exc.code) from None
        # other DomainErrors (profile unknown to the engine, reference_too_short, ...) are not dependency outages: pass through

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

    async def aclose(self) -> None:
        """API shutdown: stop the worker (and with it every job process) that this supervisor spawned."""
        await asyncio.to_thread(self._worker.stop)


def _metadata(identity_id: str) -> str:
    return json.dumps({"identity_id": identity_id}, separators=(",", ":"))
