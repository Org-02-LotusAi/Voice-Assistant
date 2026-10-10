"""Per-turn latency recording (T0..T5) and the percentile report.

T0 user stops speaking (user_state_changed speaking -> listening)   T3 first TTS PCM byte from the engine
T1 final user transcript (user_input_transcribed, is_final)          T4 agent starts speaking (agent_state_changed -> speaking)
T2 first LLM output chunk (wrapped Agent.llm_node)                   T5 filled by the e2e harness (audio heard by the human)

All stamps are `time.monotonic()` seconds on the host. On Windows (CPython 3.12) that clock ticks at ~15.6 ms, so single
intervals are quantized; percentiles over many turns are what matter.

A turn is opened when the user starts speaking. T0/T1/T3/T4 latch once per turn (the sentence-by-sentence TTS callbacks cannot
move T3). Each `llm_node` call is a *generation*; with preemptive generation a first generation may be discarded and replaced,
so T2 and `agent_text` always come from the LAST generation started before the agent began speaking (the one that is spoken).
Persistence runs through a `submit` callable (a background writer in the worker) so the realtime loop never waits on disk. Intervals are reported raw: Moonshine can
finalize its transcript before the VAD declares end of speech, which yields a negative T0->T1; that is a measurement, not an
error, and is not clamped.
"""

from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

INTERVALS: tuple[tuple[str, str, str], ...] = (
    ("t0_t1", "t0", "t1"),
    ("t1_t2", "t1", "t2"),
    ("t2_t3", "t2", "t3"),
    ("t3_t4", "t3", "t4"),
    ("t4_t5", "t4", "t5"),
)
_MAX_AGENT_TEXT = 4000


def purge_old_text(conn: sqlite3.Connection, retention_days: int, now: datetime | None = None) -> int:
    """Blank `user_text`/`agent_text` of turns older than `retention_days` (timing columns are kept). Returns rows changed."""
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=retention_days)).isoformat()
    cur = conn.execute(
        "UPDATE turn_metrics SET user_text = NULL, agent_text = NULL "
        "WHERE created_at < ? AND (user_text IS NOT NULL OR agent_text IS NOT NULL)",
        (cutoff,),
    )
    conn.commit()
    return cur.rowcount


@dataclass
class Generation:
    """One `llm_node` call: its first-chunk time and its text."""

    first_chunk_at: float | None = None
    text: list[str] = field(default_factory=list)


@dataclass
class _Turn:
    index: int
    stamps: dict[str, float] = field(default_factory=dict)
    user_text: list[str] = field(default_factory=list)
    generations: list[Generation] = field(default_factory=list)
    interrupted: bool = False
    row_id: list[int | None] = field(default_factory=lambda: [None])  # filled by the writer thread, read by later updates
    persisted: bool = False

    def agent_text(self) -> str | None:
        gen = self.generations[-1] if self.generations else None
        return ("".join(gen.text) if gen else "") or None


class LatencyRecorder:
    """Collects T0..T4 for one session and writes `turn_metrics` rows.

    `connect` returns a fresh sqlite connection (one per write, closed after). `submit` runs a write: the worker passes its
    single background writer thread's submit (ordered, non-blocking); the default runs it inline (unit tests).
    """

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        identity_id: str,
        session_id: str,
        clock: Callable[[], float] = time.monotonic,
        submit: Callable[[Callable[[], Any]], Any] | None = None,
    ) -> None:
        self._connect = connect
        self._submit = submit if submit is not None else (lambda fn: fn())
        self._identity_id = identity_id
        self._session_id = session_id
        self._clock = clock
        self._turn: _Turn | None = None
        self._next_index = 0
        self._agent_speaking = False

    # -- session events -------------------------------------------------------------------------------------------------

    def on_user_state(self, old: str, new: str) -> None:
        now = self._clock()
        if new == "speaking":
            if self._agent_speaking and self._turn is not None and "t4" in self._turn.stamps:
                self._turn.interrupted = True  # user talked over the agent's reply to the previous turn
            if self._turn is None or "t4" in self._turn.stamps:  # previous turn was answered: start a new one
                self._finalize()
                self._turn = _Turn(index=self._next_index)
                self._next_index += 1
            # a user who resumes before the agent answered stays in the same open turn; T0 is re-latched on the next pause
            self._turn.stamps.pop("t0", None)
        elif old == "speaking" and new == "listening" and self._turn is not None:
            self._turn.stamps["t0"] = now

    def on_user_transcript(self, text: str, is_final: bool) -> None:
        if not is_final or self._turn is None:
            return
        self._turn.stamps["t1"] = self._clock()
        if text.strip():
            self._turn.user_text.append(text.strip())

    def begin_generation(self) -> Generation | None:
        """Call at the start of every `llm_node`. None when no unanswered turn is open (the generation is not measured)."""
        t = self._turn
        if t is None or "t4" in t.stamps:
            return None
        gen = Generation()
        t.generations.append(gen)
        return gen

    def on_llm_chunk(self, gen: Generation | None, text: str) -> None:
        """Every output chunk of `gen`. The first chunk stamps T2 (re-stamped by a later generation until audio starts)."""
        t = self._turn
        if gen is None or t is None:
            return
        if gen.first_chunk_at is None:
            gen.first_chunk_at = self._clock()
            if "t3" not in t.stamps and "t4" not in t.stamps and gen is t.generations[-1]:
                t.stamps["t2"] = gen.first_chunk_at
        if text:
            gen.text.append(text)

    def on_tts_first_audio(self, t_monotonic: float) -> None:
        """TTS first-PCM callback (once per sentence); latches only the first one after T2 of the open turn."""
        t = self._turn
        if t is None or "t2" not in t.stamps or "t3" in t.stamps:
            return
        t.stamps["t3"] = t_monotonic

    def on_agent_state(self, old: str, new: str) -> None:
        self._agent_speaking = new == "speaking"
        t = self._turn
        if new != "speaking" or t is None or "t0" not in t.stamps or "t4" in t.stamps:
            return
        t.stamps["t4"] = self._clock()
        self._persist(t)

    def close(self) -> None:
        self._finalize()
        self._turn = None

    # -- persistence ----------------------------------------------------------------------------------------------------

    def _finalize(self) -> None:
        t = self._turn
        if t is None:
            return
        if t.persisted or t.stamps.get("t0") is not None:
            self._persist(t)  # completes the row (agent text, interrupted); also records turns the agent never answered

    def _persist(self, t: _Turn) -> None:
        """Snapshot the turn now (on the loop, cheap) and write it on the writer."""
        values = [t.stamps.get(k) for k in ("t0", "t1", "t2", "t3", "t4")]
        user_text = " ".join(t.user_text) or None
        agent_text = t.agent_text()
        if agent_text:
            agent_text = agent_text[:_MAX_AGENT_TEXT]
        interrupted = int(t.interrupted)
        row_id = t.row_id
        t.persisted = True
        index, identity_id, session_id = t.index, self._identity_id, self._session_id

        def write() -> None:
            conn = self._connect()
            try:
                if row_id[0] is None:
                    cur = conn.execute(
                        "INSERT INTO turn_metrics (identity_id, session_id, turn_index, t0, t1, t2, t3, t4, user_text, "
                        "agent_text, interrupted, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (identity_id, session_id, index, *values, user_text, agent_text, interrupted,
                         datetime.now(timezone.utc).isoformat()),
                    )
                    row_id[0] = cur.lastrowid
                else:
                    conn.execute(
                        "UPDATE turn_metrics SET t0=?, t1=?, t2=?, t3=?, t4=?, user_text=?, agent_text=?, interrupted=? "
                        "WHERE id=?",
                        (*values, user_text, agent_text, interrupted, row_id[0]),
                    )
                conn.commit()
            finally:
                conn.close()

        self._submit(write)


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (p in 0..100); None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100.0 * len(ordered)))
    return ordered[rank - 1]


def _summary(ms: list[float]) -> dict[str, Any]:
    return {"count": len(ms), "p50": percentile(ms, 50), "p95": percentile(ms, 95), "max": max(ms) if ms else None}


def set_t5(conn: sqlite3.Connection, turn_id: int, t5: float) -> None:
    """Harness hook: stamp T5 (human heard the first agent audio) on an existing turn row."""
    cur = conn.execute("UPDATE turn_metrics SET t5 = ? WHERE id = ?", (t5, turn_id))
    if cur.rowcount != 1:
        raise LookupError(f"turn_metrics row {turn_id} not found")
    conn.commit()


def report(conn: sqlite3.Connection, identity_id: str, session_id: str | None = None) -> dict[str, Any]:
    """Latency report in milliseconds: {turns, t0_t4, t0_t1, t1_t2, t2_t3, t3_t4, t4_t5}, each {count,p50,p95,max}."""
    sql = "SELECT id, turn_index, session_id, t0, t1, t2, t3, t4, t5 FROM turn_metrics WHERE identity_id = ?"
    args: list[Any] = [identity_id]
    if session_id is not None:
        sql += " AND session_id = ?"
        args.append(session_id)
    rows = conn.execute(sql + " ORDER BY id", args).fetchall()

    def deltas(a: str, b: str) -> list[float]:
        return [(r[b] - r[a]) * 1000.0 for r in rows if r[a] is not None and r[b] is not None]

    out: dict[str, Any] = {"turns": len(rows), "t0_t4": _summary(deltas("t0", "t4"))}
    for name, a, b in INTERVALS:
        out[name] = _summary(deltas(a, b))
    return out
