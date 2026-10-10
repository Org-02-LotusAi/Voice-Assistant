"""Audio helpers: WAV loading, RMS, speech-onset detection, the shared Moonshine transcriber for the Identity's audio."""

from __future__ import annotations

import asyncio
import math
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

AUDIO_DIR = Path(__file__).resolve().parents[2] / "assets" / "test_audio"
STT_RATE = 16000


def rms_of(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    f = samples.astype(np.float32)
    return float(math.sqrt(float(np.mean(f * f))))


@dataclass(frozen=True)
class Clip:
    path: Path
    rate: int
    pcm: bytes  # s16le mono
    text: str  # what the human says (the .txt next to the wav)

    @property
    def seconds(self) -> float:
        return len(self.pcm) / 2 / self.rate

    def onset_s(self, threshold: float = 300.0) -> float:
        """Offset of the first 10 ms window whose RMS exceeds `threshold` (when the speech is actually audible)."""
        step = self.rate // 100
        samples = np.frombuffer(self.pcm, dtype=np.int16)
        for i in range(0, len(samples) - step, step):
            if rms_of(samples[i : i + step]) > threshold:
                return i / self.rate
        return 0.0


def load_clip(stem: str) -> Clip:
    path = AUDIO_DIR / f"{stem}.wav"
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(f"{path.name}: expected 16-bit mono, got {w.getsampwidth() * 8}-bit x{w.getnchannels()}")
        rate, pcm = w.getframerate(), w.readframes(w.getnframes())
    text = (AUDIO_DIR / f"{stem}.txt").read_text(encoding="utf-8").strip()
    return Clip(path, rate, pcm, text)


def write_wav(path: Path, pcm: bytes, rate: int = STT_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


class Transcriber:
    """Moonshine (the same model/arch the runtime uses, via service015.stt.moonshine.load_transcriber) for offline decoding of
    the Identity's received audio. Loaded lazily on first use; decoding runs in a thread, one at a time."""

    def __init__(self, arch: str = "small", language: str = "en") -> None:
        self._arch, self._language = arch, language
        self._model: Any = None
        self._lock = threading.Lock()

    def _transcribe_blocking(self, pcm: bytes) -> str:
        with self._lock:
            if self._model is None:
                from service015.stt.moonshine import load_transcriber

                self._model = load_transcriber(self._language, self._arch)  # type: ignore[arg-type]
            samples = (np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0).tolist()
            transcript = self._model.transcribe_without_streaming(samples, STT_RATE)
            return " ".join(line.text.strip() for line in transcript.lines if line.text and line.text.strip())

    async def transcribe(self, pcm: bytes) -> str:
        if len(pcm) < STT_RATE * 2 * 2 // 5:  # < 0.4 s cannot hold words
            return ""
        return await asyncio.to_thread(self._transcribe_blocking, pcm)
