"""Live runtime acceptance for Task 9: real LiveKit server + engine sidecar + Voicebox + LLM, real worker subprocess.

Skipped unless all of them are reachable (LLM_API_KEY from env/.env). The supervisor uses the strict production preflight (no
test hook). CPU TTS is slow (first audio ~15-30 s), so the timeouts are generous. Run:  pytest tests/test_runtime_live.py -s
"""

from __future__ import annotations

import asyncio
import math
import time
import wave
from pathlib import Path

import httpx
import numpy as np
import pytest
from livekit import api, rtc

from service015 import identities_repo
from service015.config import Settings
from service015.db import connect, migrate
from service015.runtime import latency
from service015.runtime.supervisor import LiveKitSupervisor
from service015.templates_repo import seed_starter_templates
from service015.voice.authz import record_authorization
from service015.voice.voicebox import VoiceboxClient

ROOT = Path(__file__).resolve().parents[1]
LIVE = Settings(_env_file=ROOT / ".env")
AVA_PROFILE = "6326829e-38ae-475f-aedb-5c293aa43e51"  # seeded "Ava Warm"
QUESTION = ROOT / "assets" / "test_audio" / "q_name.wav"


def _reachable() -> tuple[bool, str]:
    if not LIVE.llm_api_key:
        return False, "LLM_API_KEY not set"
    try:
        h = httpx.get(LIVE.voice_engine_url + "/health", timeout=3).json()
        if h.get("status") != "ok":
            return False, "voice engine not ready"
        if httpx.get(LIVE.voicebox_url + "/profiles", timeout=3).status_code != 200:
            return False, "Voicebox not reachable"
        httpx.get(LIVE.livekit_url.replace("ws://", "http://").replace("wss://", "https://"), timeout=3)
    except (httpx.HTTPError, ValueError) as exc:
        return False, f"dependency not reachable: {type(exc).__name__}"
    return True, ""


_OK, _WHY = _reachable()
pytestmark = pytest.mark.skipif(not _OK, reason=_WHY)


def _rms(frame: rtc.AudioFrame) -> float:
    pcm = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32)
    return float(math.sqrt(float(np.mean(pcm * pcm)))) if pcm.size else 0.0


class Human:
    """A scripted human participant: joins a room, records the agent's audio energy, can speak a wav file."""

    def __init__(self, settings: Settings, room_name: str) -> None:
        self.room = rtc.Room()
        self.identity = "human-test0001"
        self.settings, self.room_name = settings, room_name
        self.energy: list[tuple[float, float]] = []  # (monotonic, rms) per received agent frame
        self._tasks: list[asyncio.Task] = []
        self.subscribed: list[str] = []

    async def join(self) -> None:
        s = self.settings
        token = (
            api.AccessToken(s.livekit_api_key, s.livekit_api_secret)
            .with_identity(self.identity)
            .with_grants(api.VideoGrants(room_join=True, room=self.room_name))
            .to_jwt()
        )

        @self.room.on("track_subscribed")
        def _sub(track: rtc.Track, pub, participant: rtc.RemoteParticipant) -> None:
            self.subscribed.append(f"{participant.identity}:{track.kind}")
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                self._tasks.append(asyncio.create_task(self._collect(rtc.AudioStream(track))))

        await self.room.connect(s.livekit_url, token)

    async def _collect(self, stream: rtc.AudioStream) -> None:
        async for ev in stream:
            self.energy.append((time.monotonic(), _rms(ev.frame)))

    def speaking_seconds_since(self, t: float, threshold: float = 300.0) -> float:
        return sum(0.01 for ts, r in self.energy if ts >= t and r > threshold)  # frames are 10 ms

    def last_energy_at(self, threshold: float = 300.0) -> float | None:
        loud = [ts for ts, r in self.energy if r > threshold]
        return loud[-1] if loud else None

    async def say(self, wav_path: Path) -> None:
        with wave.open(str(wav_path)) as w:
            rate, pcm = w.getframerate(), w.readframes(w.getnframes())
        source = rtc.AudioSource(rate, 1)
        track = rtc.LocalAudioTrack.create_audio_track("human-mic", source)
        await self.room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        silence = b"\x00\x00" * (rate // 2)
        step = rate // 100 * 2  # 10 ms of s16 mono
        for data in (silence, pcm, silence * 4):  # lead-in and tail silence so VAD sees clean edges
            for i in range(0, len(data), step):
                chunk = data[i : i + step]
                frame = rtc.AudioFrame(chunk, rate, 1, len(chunk) // 2)
                await source.capture_frame(frame)
        await source.wait_for_playout()
        await self.room.local_participant.unpublish_track(track.sid)

    async def leave(self) -> None:
        await self.room.disconnect()
        for t in self._tasks:
            t.cancel()


async def _gone(s: Settings) -> bool:
    return "ava-001" not in await _participants(s, "ava-001")


async def _wait(predicate, timeout: float, what: str, interval: float = 0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return value
        await asyncio.sleep(interval)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what}")


def _scenario_settings(tmp_path) -> Settings:
    return LIVE.model_copy(update={"db_path": str(tmp_path / "live.db")})


async def _participants(s: Settings, room: str) -> dict[str, rtc.ParticipantInfo]:
    lk = api.LiveKitAPI(s.livekit_url, s.livekit_api_key, s.livekit_api_secret)
    try:
        res = await lk.room.list_participants(api.ListParticipantsRequest(room=room))
        return {p.identity: p for p in res.participants}
    finally:
        await lk.aclose()


def test_launch_speak_turn_stop_relaunch(tmp_path):
    s = _scenario_settings(tmp_path)
    conn = connect(s.db_path)
    migrate(conn)
    seed_starter_templates(conn)
    record_authorization(conn, AVA_PROFILE, "live-test", "Zira (synthetic)", "Synthetic OS TTS voice.", "seed")
    identities_repo.instantiate(
        conn, "sales-representative", {"name": "Ava", "company": "Safi", "role": "Account Specialist", "voice.profile_id": AVA_PROFILE}
    )
    sup = LiveKitSupervisor(s)

    async def go() -> dict:
        out: dict = {}
        human = Human(s, "ava-001")  # rtc.Room must be created inside the running loop
        try:
            t_launch = time.monotonic()
            st = await sup.launch("ava-001")
            assert st["presence"] == "STARTING" and st["participant_identity"] == st["default_room"] == "ava-001"
            states = {st["presence"]}

            def ready():
                cur = identities_repo.get_status(conn, "ava-001")
                states.add(cur.presence)
                return cur.presence in ("READY", "BUSY") and cur
            await _wait(ready, 240, "READY/BUSY")
            out["ready_after_s"] = time.monotonic() - t_launch
            assert "STARTING" in states
            parts = await _participants(s, "ava-001")
            assert "ava-001" in parts, parts.keys()
            assert parts["ava-001"].name == "Ava"
            assert parts["ava-001"].kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
            assert identities_repo.get_status(conn, "ava-001").presence == "READY"  # nobody in the room yet
            await asyncio.sleep(8)  # the greeting must wait for a listener (asserted below by hearing it only after join)

            # the greeting is spoken (TTS only, no LLM) when the first human joins
            t_join = time.monotonic()
            await human.join()
            await _wait(lambda: identities_repo.get_status(conn, "ava-001").presence == "BUSY", 20, "BUSY after join")
            try:
                await _wait(lambda: human.speaking_seconds_since(t_join) > 1.0, 150, "greeting audio")
            except AssertionError:
                parts = await _participants(s, "ava-001")
                tracks = [(t.type, t.source, t.muted) for t in parts["ava-001"].tracks] if "ava-001" in parts else None
                peak = max((r for _, r in human.energy), default=None)
                raise AssertionError(
                    f"no greeting audio: frames={len(human.energy)} peak_rms={peak} agent_tracks={tracks} "
                    f"human_remote={list(human.room.remote_participants)} subscribed={human.subscribed}"
                ) from None
            out["greeting_audio_after_join_s"] = time.monotonic() - t_join
            # wait for the greeting to finish before asking (otherwise it is a barge-in)
            await _wait(lambda: (human.last_energy_at() or 0) < time.monotonic() - 4, 240, "greeting to finish")

            # a real turn: audio in -> Moonshine -> LLM -> Chatterbox -> audio out, with T0..T4 recorded
            t_q = time.monotonic()
            await human.say(QUESTION)
            await _wait(lambda: human.speaking_seconds_since(t_q + 1.0) > 0.8, 240, "spoken answer")
            await asyncio.sleep(6)  # let the recorder finalize the row

            row = conn.execute("SELECT * FROM turn_metrics WHERE identity_id = 'ava-001' ORDER BY id").fetchall()
            assert row, "no turn recorded"
            turn = row[0]
            out["turn"] = {k: turn[k] for k in ("t0", "t1", "t2", "t3", "t4", "user_text", "agent_text")}
            out["report"] = latency.report(conn, "ava-001")
            assert all(turn[k] is not None for k in ("t0", "t1", "t2", "t3", "t4")), dict(turn)
            assert "name" in turn["user_text"].lower()
            assert "ava" in turn["agent_text"].lower()
            assert turn["t0"] <= turn["t4"]
            sess = conn.execute("SELECT * FROM runtime_sessions").fetchall()
            assert len(sess) == 1 and sess[0]["room"] == "ava-001" and sess[0]["ended_at"] is None

            # human leaves -> READY again
            await human.leave()
            await _wait(lambda: identities_repo.get_status(conn, "ava-001").presence == "READY", 20, "READY after human left")
            hb1 = identities_repo.get_status(conn, "ava-001").heartbeat_at
            await asyncio.sleep(6)
            assert identities_repo.get_status(conn, "ava-001").heartbeat_at != hb1, "heartbeat is not advancing"

            # idempotent launch while running
            again = await sup.launch("ava-001")
            assert again["presence"] in ("READY", "BUSY")

            # stop -> participant removed, OFFLINE
            st = await sup.stop("ava-001")
            assert st["presence"] == "OFFLINE"
            await _wait(lambda: _gone(s), 30, "agent participant removed")
            await _wait(lambda: conn.execute("SELECT ended_at FROM runtime_sessions").fetchone()[0], 30, "session end recorded")
            assert identities_repo.get_status(conn, "ava-001").presence == "OFFLINE"

            # relaunch -> same identity and room
            st = await sup.launch("ava-001")
            assert st["presence"] == "STARTING"
            await _wait(lambda: identities_repo.get_status(conn, "ava-001").presence == "READY", 240, "READY after relaunch")
            parts = await _participants(s, "ava-001")
            assert "ava-001" in parts and parts["ava-001"].kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
            assert len(conn.execute("SELECT * FROM runtime_sessions").fetchall()) == 2

            # stop the WHOLE worker (API shutdown path): jobs drain, rows go OFFLINE, sessions get ended_at
            await sup.aclose()
            assert identities_repo.get_status(conn, "ava-001").presence == "OFFLINE"
            ended = conn.execute("SELECT ended_at, end_reason FROM runtime_sessions ORDER BY started_at").fetchall()
            assert all(r["ended_at"] for r in ended), [dict(r) for r in ended]
            out["session_end_reasons"] = [r["end_reason"] for r in ended]
            return out
        finally:
            try:
                await human.leave()
            except Exception:
                pass
            try:
                await sup.stop("ava-001")
            finally:
                await sup.aclose()

    out = asyncio.run(asyncio.wait_for(go(), 1500))
    print("\nLIVE:", out)
    assert not sup._worker.alive()
    conn.close()

