"""Deepgram Aura-2 TTS for the LiveKit session: the official plugin (streaming WebSocket) plus the T3 latency hook.

`livekit.plugins.deepgram.TTS` streams text over a WebSocket and pushes PCM into the framework as Deepgram produces it, so audio
starts on the first chunk. This subclass changes nothing about that; it only reports the first PCM byte of each text segment to
`on_first_audio(t_monotonic)` (the same contract as `ChatterboxTTS`, so `LatencyRecorder.on_tts_first_audio` works unchanged).
A telemetry callback that raises is logged and never stops speech.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from livekit.agents import APIConnectOptions, tokenize, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
from livekit.plugins import deepgram

log = logging.getLogger("service015.voice")

FirstAudioCallback = Callable[[float], None]


class _FirstAudioEmitter:
    """Proxy for the framework's AudioEmitter: forwards everything, fires the callback on the first non-empty `push`."""

    def __init__(self, inner: Any, callback: FirstAudioCallback | None) -> None:
        self._inner = inner
        self._callback = callback
        self._fired = False

    def push(self, data: bytes) -> None:
        if data and not self._fired:
            self._fired = True
            if self._callback is not None:
                try:
                    self._callback(time.monotonic())
                except Exception:
                    log.exception("on_first_audio callback raised")
        self._inner.push(data)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class AuraTTS(deepgram.TTS):
    def __init__(self, *, model: str, api_key: str, on_first_audio: FirstAudioCallback | None = None, **kwargs: Any) -> None:
        super().__init__(model=model, api_key=api_key, **kwargs)
        self.on_first_audio = on_first_audio

    @property
    def provider(self) -> str:
        return "deepgram"

    def stream(self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS) -> "AuraSynthesizeStream":
        stream = AuraSynthesizeStream(tts=self, conn_options=conn_options)
        self._streams.add(stream)
        return stream

    def synthesize(self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS) -> "AuraChunkedStream":
        return AuraChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class AuraSynthesizeStream(deepgram.tts.SynthesizeStream):
    async def _run_ws(self, word_stream: tokenize.WordStream, output_emitter: tts.AudioEmitter) -> None:
        # one proxy per text segment: the recorder latches the first one of the turn
        await super()._run_ws(word_stream, _FirstAudioEmitter(output_emitter, self._tts.on_first_audio))  # type: ignore[arg-type]


class AuraChunkedStream(deepgram.tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        await super()._run(_FirstAudioEmitter(output_emitter, self._tts.on_first_audio))  # type: ignore[arg-type]
