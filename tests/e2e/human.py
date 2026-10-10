"""The scripted human: a LiveKit participant that joins with an API-issued token, speaks pre-rendered wavs and records everything
the Identity publishes (energy per frame, raw PCM at 16 kHz, the `lk.agent.state` attribute changes)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import numpy as np
from livekit import rtc

from .audio import STT_RATE, Clip, rms_of

AGENT_STATE_ATTR = "lk.agent.state"  # livekit.agents.types.ATTRIBUTE_AGENT_STATE; literal so the harness needs no agents import


@dataclass
class Utterance:
    started_t: float  # first frame handed to LiveKit (includes lead-in silence)
    onset_t: float  # the frame where the speech becomes audible
    end_t: float  # when the last speech frame was handed over
    clip: str


@dataclass
class Frame:
    t: float
    rms: float
    pcm: bytes


@dataclass
class Human:
    livekit_url: str
    token: str
    agent_identity: str
    rms_threshold: float = 300.0
    room: rtc.Room | None = None
    frames: list[Frame] = field(default_factory=list)
    states: list[tuple[float, str]] = field(default_factory=list)
    subscribed: list[str] = field(default_factory=list)
    _tasks: list[asyncio.Task] = field(default_factory=list)
    _source: rtc.AudioSource | None = None
    _rate: int = 0
    _mic_sid: str | None = None

    async def connect(self) -> None:
        self.room = rtc.Room()  # must be created inside the running loop
        room = self.room

        @room.on("track_subscribed")
        def _sub(track: rtc.Track, pub, participant: rtc.RemoteParticipant) -> None:
            self.subscribed.append(f"{participant.identity}:{track.kind}")
            if participant.identity == self.agent_identity and track.kind == rtc.TrackKind.KIND_AUDIO:
                stream = rtc.AudioStream(track, sample_rate=STT_RATE, num_channels=1)
                self._tasks.append(asyncio.create_task(self._collect(stream)))

        @room.on("participant_attributes_changed")
        def _attrs(changed: dict, participant: rtc.Participant) -> None:
            if participant.identity == self.agent_identity and AGENT_STATE_ATTR in changed:
                self.states.append((time.monotonic(), changed[AGENT_STATE_ATTR]))

        await room.connect(self.livekit_url, self.token)
        for p in room.remote_participants.values():  # agent already present: pick up its current state
            if p.identity == self.agent_identity and AGENT_STATE_ATTR in p.attributes:
                self.states.append((time.monotonic(), p.attributes[AGENT_STATE_ATTR]))

    async def _collect(self, stream: rtc.AudioStream) -> None:
        async for ev in stream:
            data = bytes(ev.frame.data)
            self.frames.append(Frame(time.monotonic(), rms_of(np.frombuffer(data, dtype=np.int16)), data))

    # -- listening ----------------------------------------------------------------------------------------------------------

    def agent_present(self) -> bool:
        return self.room is not None and self.agent_identity in self.room.remote_participants

    def current_state(self) -> str | None:
        return self.states[-1][1] if self.states else None

    def state_since(self) -> float | None:
        return self.states[-1][0] if self.states else None

    def first_loud_after(self, t: float) -> float | None:
        for f in self.frames:
            if f.t > t and f.rms > self.rms_threshold:
                return f.t
        return None

    def last_loud(self) -> float | None:
        for f in reversed(self.frames):
            if f.rms > self.rms_threshold:
                return f.t
        return None

    def silence_after(self, t: float, gap_s: float) -> float | None:
        """Time of the last loud frame of the first run of audio after `t` that is followed by >= gap_s of quiet (either quiet
        frames, a pause in frame delivery, or a new loud run); None while audio is still playing."""
        last_loud: float | None = None
        for f in self.frames:
            if f.t <= t:
                continue
            if f.rms > self.rms_threshold:
                if last_loud is not None and f.t - last_loud >= gap_s:
                    return last_loud
                last_loud = f.t
            elif last_loud is not None and f.t - last_loud >= gap_s:
                return last_loud
        if last_loud is not None and time.monotonic() - last_loud >= gap_s:
            return last_loud
        return None

    def pcm_between(self, a: float, b: float) -> bytes:
        return b"".join(f.pcm for f in self.frames if a <= f.t <= b)

    def loud_seconds(self, a: float, b: float) -> float:
        return sum(len(f.pcm) / 2 / STT_RATE for f in self.frames if a <= f.t <= b and f.rms > self.rms_threshold)

    # -- speaking -----------------------------------------------------------------------------------------------------------

    async def _ensure_mic(self, rate: int) -> rtc.AudioSource:
        if self._source is not None and self._rate == rate:
            return self._source
        assert self.room is not None
        if self._mic_sid is not None:
            await self.room.local_participant.unpublish_track(self._mic_sid)
        self._source = rtc.AudioSource(rate, 1, queue_size_ms=100)  # small queue so capture time ~ playout time
        self._rate = rate
        track = rtc.LocalAudioTrack.create_audio_track("human-mic", self._source)
        pub = await self.room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        self._mic_sid = pub.sid
        return self._source

    async def say(self, clip: Clip, *, lead_s: float = 0.3, tail_s: float = 2.0) -> Utterance:
        """Speak `clip` in real time, bracketed by silence so the agent's VAD sees clean edges."""
        source = await self._ensure_mic(clip.rate)
        step = clip.rate // 100  # 10 ms frames
        silence = np.zeros(step, dtype=np.int16).tobytes()
        speech_frames = [clip.pcm[i : i + step * 2] for i in range(0, len(clip.pcm), step * 2)]
        lead_frames = int(lead_s * 100)
        onset_frame = lead_frames + int(clip.onset_s(self.rms_threshold) * 100)
        started = onset_t = 0.0
        index = 0

        async def push(chunk: bytes) -> None:
            nonlocal index, started, onset_t
            if len(chunk) < step * 2:
                chunk = chunk + b"\x00" * (step * 2 - len(chunk))
            if index == 0:
                started = time.monotonic()
            if index == onset_frame:
                onset_t = time.monotonic()
            await source.capture_frame(rtc.AudioFrame(chunk, clip.rate, 1, step))
            index += 1

        for _ in range(lead_frames):
            await push(silence)
        for chunk in speech_frames:
            await push(chunk)
        end_t = time.monotonic()
        for _ in range(int(tail_s * 100)):
            await push(silence)
        return Utterance(started_t=started, onset_t=onset_t or started, end_t=end_t, clip=clip.path.stem)

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self.room is not None:
            await self.room.disconnect()
