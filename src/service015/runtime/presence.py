"""Presence state machine for one running Identity job: writes `runtime_status` (the single source of truth).

Target presence is derived, never set ad hoc:
  reconnecting -> ERROR / RECONNECTING;  component error -> ERROR / <detail>;  human present -> BUSY;  else READY.

Ownership: the job claims the status row (pid) when it starts. If the row no longer shows this pid (the supervisor stopped the
Identity, or a newer job took over) every later write is refused with `Superseded`, so a stale job can never resurrect an
OFFLINE Identity or clobber a relaunch.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from service015 import identities_repo

log = logging.getLogger("service015.runtime")

# status_detail values (SOW section 27): never fabricated speech, always an explicit coded failure.
LLM_UNAVAILABLE = "llm_unavailable"
STT_FAILURE = "stt_failure"
TTS_UNAVAILABLE = "tts_unavailable"
VOICE_REQUIRED = "voice_required"
RECONNECTING = "RECONNECTING"
LIVEKIT_DISCONNECTED = "livekit_disconnected"
RUNTIME_LOST = "runtime_lost"
# Shutdown reason the SDK passes to shutdown callbacks when the room connection is permanently gone.
ROOM_DISCONNECTED_REASON = "room disconnected"


class Superseded(Exception):
    """The status row is no longer owned by this job (stopped or replaced)."""


class JobWriter:
    """Single writer thread for every SQLite write a job makes, so the realtime event loop never waits on disk.

    `submit` is fire-and-forget and keeps submission order; a `Superseded` raised by a queued write calls `on_superseded`
    on the loop (the job shuts itself down), any other failure is logged loudly. `run` awaits a write's result.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, on_superseded: Callable[[], None], name: str = "job") -> None:
        self._loop = loop
        self._on_superseded = on_superseded
        self._ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"db-writer-{name}")

    def submit(self, fn: Callable[..., Any], *args: Any) -> Future:
        fut = self._ex.submit(fn, *args)
        fut.add_done_callback(self._done)
        return fut

    def _done(self, fut: Future) -> None:
        exc = fut.exception()
        if exc is None:
            return
        if isinstance(exc, Superseded):
            self._loop.call_soon_threadsafe(self._on_superseded)
        else:
            log.error("background database write failed", exc_info=exc)

    async def run(self, fn: Callable[..., Any], *args: Any) -> Any:
        return await asyncio.wrap_future(self._ex.submit(fn, *args))

    def close(self) -> None:
        self._ex.shutdown(wait=True)


class PresenceController:
    def __init__(self, conn: sqlite3.Connection, identity_id: str, pid: int | None = None) -> None:
        self._conn = conn
        self._identity_id = identity_id
        self._pid = pid if pid is not None else os.getpid()
        self._humans: set[str] = set()
        self._error: tuple[str, str] | None = None
        self._reconnecting = False
        self._claimed = False
        self._terminal = False

    @property
    def pid(self) -> int:
        return self._pid

    # -- ownership ------------------------------------------------------------------------------------------------------

    def claim(self) -> None:
        """Take the row at job start. Raises Superseded if the Identity is OFFLINE (stopped before the job began)."""
        st = identities_repo.get_status(self._conn, self._identity_id)
        if st.presence == "OFFLINE":
            raise Superseded(f"{self._identity_id} was stopped before the job started")
        identities_repo.set_status(self._conn, self._identity_id, "STARTING", st.status_detail, None, pid=self._pid)
        self._claimed = True

    def _check_owner(self) -> None:
        st = identities_repo.get_status(self._conn, self._identity_id)
        if not self._claimed or st.pid != self._pid or st.presence == "OFFLINE":
            raise Superseded(f"{self._identity_id} is no longer owned by pid {self._pid}")

    # -- inputs ---------------------------------------------------------------------------------------------------------

    def human_joined(self, identity: str) -> None:
        self._humans.add(identity)
        self.refresh()

    def human_left(self, identity: str) -> None:
        self._humans.discard(identity)
        self.refresh()

    def set_reconnecting(self, value: bool) -> None:
        self._reconnecting = value
        self.refresh()

    def fail(self, detail: str, message: str, terminal: bool = False) -> None:
        """Latch a component error (ERROR + status_detail) until `recovered()`.

        `terminal` marks a failure after which the job cannot work (startup failure): when the job then ends, the ERROR row
        is left in place instead of being turned into OFFLINE.
        """
        self._error = (detail, message)
        self._terminal = self._terminal or terminal
        self.refresh()

    def set_humans(self, identities: "frozenset[str] | set[str]") -> None:
        self._humans = set(identities)
        self.refresh()

    def recovered(self, detail: str | None = None) -> None:
        """Clear the latched component error (all of them when `detail` is None), then refresh presence."""
        if self._error is not None and (detail is None or self._error[0] == detail):
            self._error = None
            self.refresh()

    def note_error(self, message: str) -> None:
        """Record a recoverable error in `last_error` without changing presence."""
        self._check_owner()
        st = identities_repo.get_status(self._conn, self._identity_id)
        identities_repo.set_status(self._conn, self._identity_id, st.presence, st.status_detail, message)

    def add_human(self, identity: str) -> None:
        """Count a human already in the room at session start (no presence write; `refresh()` follows)."""
        self._humans.add(identity)

    @property
    def has_humans(self) -> bool:
        return bool(self._humans)

    @property
    def error_detail(self) -> str | None:
        return self._error[0] if self._error else None

    # -- outputs --------------------------------------------------------------------------------------------------------

    def target(self) -> tuple[str, str | None, str | None]:
        if self._reconnecting:
            return "ERROR", RECONNECTING, "LiveKit connection lost; reconnecting"
        if self._error is not None:
            return "ERROR", self._error[0], self._error[1]
        return ("BUSY" if self._humans else "READY"), None, None

    def refresh(self) -> None:
        self._check_owner()
        presence, detail, error = self.target()
        identities_repo.set_status(self._conn, self._identity_id, presence, detail, error)
        identities_repo.heartbeat(self._conn, self._identity_id)

    def heartbeat(self) -> None:
        self._check_owner()
        identities_repo.heartbeat(self._conn, self._identity_id)

    def starting(self, detail: str | None = None) -> None:
        self._check_owner()
        identities_repo.set_status(self._conn, self._identity_id, "STARTING", detail, None)

    def release(self) -> bool:
        """Job end: OFFLINE, but only if this job still owns the row. Returns whether it wrote."""
        try:
            self._check_owner()
        except Superseded:
            return False
        identities_repo.set_status(self._conn, self._identity_id, "OFFLINE")
        return True

    def end(self, reason: str) -> str | None:
        """Job end. OFFLINE is reserved for a deliberate stop; losing the LiveKit room is a fault.

        Returns the presence written ("OFFLINE" or "ERROR"), or None when this job no longer owns the row (the operator
        already stopped it, or a newer job took over), in which case nothing is written.
        """
        try:
            self._check_owner()
        except Superseded:
            return None
        if reason == ROOM_DISCONNECTED_REASON:
            identities_repo.set_status(
                self._conn, self._identity_id, "ERROR", LIVEKIT_DISCONNECTED, "the LiveKit room connection was lost; relaunch to rejoin"
            )
            return "ERROR"
        if self._terminal:
            return "ERROR"  # a startup failure stays visible; only an explicit stop/relaunch clears it
        identities_repo.set_status(self._conn, self._identity_id, "OFFLINE")
        return "OFFLINE"


def humans_in(room_participants: dict, own_identity: str, human_kinds: Callable[[object], bool]) -> set[str]:
    return {p.identity for p in room_participants.values() if p.identity != own_identity and human_kinds(p)}
