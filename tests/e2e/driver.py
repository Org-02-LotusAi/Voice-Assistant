"""Conversation driver: launch/join/ask building blocks shared by every scenario, and the evidence recorder."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from service015.db import connect
from service015.runtime import latency

from .audio import Transcriber, load_clip, write_wav
from .human import Human, Utterance
from .stack import ApiClient, ApiProcess, Config

QUIET_FALLBACK_S = 30.0  # used only when the agent publishes no lk.agent.state attribute at all


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ScenarioResult:
    name: str
    test_ref: str
    status: str = "PASS"  # PASS | FAIL | FAIL-ON-THIS-HOST (latency ceiling missed on a host that cannot meet it)
    checks: list[Check] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0
    error: str | None = None

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(name, bool(ok), detail))
        return bool(ok)

    def finalize(self) -> None:
        if self.error or any(not c.ok for c in self.checks):
            if self.status == "PASS":
                self.status = "FAIL"


@dataclass
class Reply:
    started: bool
    t_first: float | None = None
    t_last: float | None = None
    complete: bool = False
    pcm: bytes = b""
    transcript: str = ""


@dataclass
class TurnResult:
    label: str
    clip: str
    human_says: str
    onset_t: float
    reply: Reply
    row: dict[str, Any] | None
    t5: float | None
    wait_start_s: float | None
    wait_complete_s: float | None

    def as_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["reply"].pop("pcm")
        d["reply"]["audio_seconds"] = round(len(self.reply.pcm) / 2 / 16000, 2)
        return d

    @property
    def agent_text(self) -> str:
        return (self.row or {}).get("agent_text") or ""

    @property
    def heard(self) -> str:
        return self.reply.transcript


class Ctx:
    def __init__(self, cfg: Config, api: ApiProcess, evidence_dir: Path, raw_dir: Path) -> None:
        self.cfg, self.api, self.evidence_dir, self.raw_dir = cfg, api, evidence_dir, raw_dir
        self.client = ApiClient(api.base_url)
        self.viewer = ApiClient(api.base_url, "viewer")
        self.tr = Transcriber(cfg.moonshine_arch)
        self.conn: sqlite3.Connection | None = None
        self.cache: dict[str, Any] = {}
        self.identities: dict[str, dict[str, Any]] = {}
        self.open_humans: list[Human] = []

    # -- db -----------------------------------------------------------------------------------------------------------------

    def db(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = connect(str(self.api.db_path))
        return self.conn

    def max_turn_id(self, identity_id: str) -> int:
        row = self.db().execute("SELECT COALESCE(MAX(id), 0) FROM turn_metrics WHERE identity_id = ?", (identity_id,)).fetchone()
        return int(row[0])

    # -- identities ---------------------------------------------------------------------------------------------------------

    IDENTITY_SPECS = {
        "ava": {"name": "Ava", "company": "Safi", "role": "Account Specialist", "voice": "ava_voice"},
        "james": {"name": "James", "company": "Northwind", "role": "Solutions Consultant", "voice": "james_voice"},
    }

    async def ensure_identity(self, key: str) -> dict[str, Any]:
        if key in self.identities:
            return self.identities[key]
        spec = self.IDENTITY_SPECS[key]
        body = {
            "overrides": {
                "name": spec["name"], "company": spec["company"], "role": spec["role"],
                "voice.profile_id": getattr(self.cfg, spec["voice"]),
            }
        }
        ident = await self.client.json("POST", "/templates/sales-representative/instantiate", 201, json=body)
        self.identities[key] = ident
        return ident

    def spec(self, key: str) -> dict[str, str]:
        return self.IDENTITY_SPECS[key]

    async def status(self, identity_id: str) -> dict[str, Any]:
        return await self.client.json("GET", f"/identities/{identity_id}/status")

    async def launch_ready(self, identity_id: str) -> dict[str, Any]:
        """POST launch, then poll status until READY/BUSY. Raises with the status detail on ERROR/timeout."""
        t0 = time.monotonic()
        retries: list[str] = []
        while True:
            r = await self.client.request("POST", f"/identities/{identity_id}/launch")
            err = r.json().get("error", {}) if r.status_code != 200 else {}
            # The cold runtime worker must register with LiveKit within the supervisor's fixed 60 s; on a memory-starved CPU host the
            # first spawn can miss that. The supervisor stops the half-started worker and a relaunch spawns a fresh one: retry
            # twice, and record every retry in the evidence (never hidden).
            if r.status_code == 503 and err.get("code") == "runtime_unavailable" and len(retries) < 2:
                retries.append(err.get("message", ""))
                continue
            if r.status_code != 200:
                raise AssertionError(f"launch {identity_id} -> {r.status_code}: {r.text[:400]}")
            first = r.json()
            break
        deadline = t0 + self.cfg.launch_timeout_s
        seen = [first["presence"]]
        while time.monotonic() < deadline:
            st = await self.status(identity_id)
            if st["presence"] != seen[-1]:
                seen.append(st["presence"])
            if st["presence"] in ("READY", "BUSY"):
                return {"ready_after_s": round(time.monotonic() - t0, 1), "presence_seen": seen, "status": st, "start_retries": retries}
            if st["presence"] == "ERROR":
                # start_timeout: the job process took > 120 s to initialize (cold Moonshine + silero load on a starved CPU host).
                # launch() treats it as relaunchable; retry once and record it.
                if st["status_detail"] == "start_timeout" and len(retries) < 2:
                    retries.append(f"start_timeout: {st['last_error']}")
                    r = await self.client.request("POST", f"/identities/{identity_id}/launch")
                    if r.status_code != 200:
                        raise AssertionError(f"relaunch after start_timeout -> {r.status_code}: {r.text[:300]}")
                    deadline = time.monotonic() + self.cfg.launch_timeout_s
                    continue
                raise AssertionError(f"{identity_id} went to ERROR during launch: {st['status_detail']} / {st['last_error']}")
            await asyncio.sleep(1.0)
        raise AssertionError(f"{identity_id} not READY within {self.cfg.launch_timeout_s:.0f}s (seen {seen})")

    async def stop(self, identity_id: str) -> dict[str, Any]:
        return await self.client.json("POST", f"/identities/{identity_id}/stop")

    # -- joining and listening ----------------------------------------------------------------------------------------------

    async def join(self, identity_id: str, *, wait_greeting: bool = True) -> tuple[Human, dict[str, Any]]:
        """Join the Default Room with an API-issued token; optionally wait for the greeting to finish playing."""
        jt = await self.client.json("POST", f"/identities/{identity_id}/join-token")
        ident = await self.client.json("GET", f"/identities/{identity_id}")
        agent_identity = ident["participant"]["participant_identity"]
        human = Human(jt["livekit_url"], jt["token"], agent_identity, self.cfg.rms_threshold)
        self.open_humans.append(human)
        t_join = time.monotonic()
        await human.connect()
        info: dict[str, Any] = {"human_identity": jt["participant_identity"], "room": jt["room"]}
        if wait_greeting:
            reply = await self.wait_reply(human, t_join, self.cfg.greeting_timeout_s, self.cfg.reply_complete_timeout_s)
            info["greeting_started_after_join_s"] = round(reply.t_first - t_join, 1) if reply.t_first else None
            info["greeting_complete"] = reply.complete
            if reply.started:
                reply.transcript = await self.tr.transcribe(reply.pcm)
            info["greeting_transcript"] = reply.transcript
            info["greeting_audio_s"] = round(len(reply.pcm) / 2 / 16000, 2)
        return human, info

    async def leave(self, human: Human) -> None:
        try:
            await human.close()
        finally:
            if human in self.open_humans:
                self.open_humans.remove(human)

    async def wait_reply(self, h: Human, after_t: float, start_timeout: float, complete_timeout: float) -> Reply:
        """Wait for the Identity's audio to start after `after_t`, then until its reply has finished (agent state back to
        listening/idle and quiet for settle_s)."""
        t_wait = time.monotonic()
        t_first: float | None = None
        while time.monotonic() - t_wait < start_timeout:
            t_first = h.first_loud_after(after_t)
            if t_first is not None:
                break
            await asyncio.sleep(0.25)
        if t_first is None:
            return Reply(started=False)
        deadline = time.monotonic() + complete_timeout
        complete = False
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            now = time.monotonic()
            last = h.last_loud() or t_first
            quiet_for = now - last
            state, since = h.current_state(), h.state_since()
            have_states = any(t > after_t for t, _ in h.states)
            if have_states:
                if state in ("listening", "idle") and since is not None and since >= t_first and now - since >= self.cfg.settle_s and quiet_for >= self.cfg.settle_s:
                    complete = True
                    break
            elif quiet_for >= QUIET_FALLBACK_S:
                complete = True
                break
        t_last = h.last_loud() or t_first
        return Reply(True, t_first, t_last, complete, h.pcm_between(t_first - 0.2, t_last + 0.5))

    # -- one human turn -----------------------------------------------------------------------------------------------------

    async def ask(self, h: Human, identity_id: str, stem: str, scenario: str, label: str | None = None) -> TurnResult:
        label = label or stem
        clip = load_clip(stem)
        before = self.max_turn_id(identity_id)
        utt: Utterance = await h.say(clip)
        t_sent = time.monotonic()
        reply = await self.wait_reply(h, utt.onset_t, self.cfg.reply_start_timeout_s, self.cfg.reply_complete_timeout_s)
        t_done = time.monotonic()
        if reply.started:
            reply.transcript = await self.tr.transcribe(reply.pcm)
            write_wav(self.raw_dir / scenario / f"{label}-reply.wav", reply.pcm)
        row = await self._turn_row(identity_id, before)
        t5 = reply.t_first
        if row is not None and t5 is not None:
            latency.set_t5(self.db(), row["id"], t5)
            row["t5"] = t5
        return TurnResult(
            label, stem, clip.text, utt.onset_t, reply, row, t5,
            round(reply.t_first - utt.end_t, 2) if reply.t_first else None,
            round(t_done - t_sent, 2),
        )

    async def _turn_row(self, identity_id: str, before: int, timeout: float = 25.0) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while True:
            rows = self.db().execute(
                "SELECT * FROM turn_metrics WHERE identity_id = ? AND id > ? ORDER BY id", (identity_id, before)
            ).fetchall()
            done = [dict(r) for r in rows if r["t4"] is not None]
            if done or time.monotonic() > deadline:
                if done:
                    return done[0]
                return dict(rows[-1]) if rows else None
            await asyncio.sleep(0.5)

    # -- evidence -----------------------------------------------------------------------------------------------------------

    def write_json(self, name: str, payload: Any) -> None:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        (self.evidence_dir / name).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    async def cleanup(self) -> None:
        for h in list(self.open_humans):
            try:
                await self.leave(h)
            except Exception:
                pass
        await self.client.aclose()
        await self.viewer.aclose()
        if self.conn is not None:
            self.conn.close()
