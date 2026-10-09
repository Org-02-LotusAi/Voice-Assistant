"""LiveKit Agents STT adapter over Moonshine Voice streaming transcription (moonshine-voice 0.1.5).

One Moonshine `Transcriber` (the loaded model) is shared per process and injected; every `RecognizeStream` owns its own
Moonshine `Stream`. Moonshine's C calls are blocking, so each LiveKit stream feeds audio from a dedicated thread and the
thread forwards transcript events back to the event loop; the loop never waits on the model.

Thread safety: the native library gives no documented guarantee for concurrent calls on one Transcriber, so every native call
(create_stream/start/add_audio/stop/close) is serialized by a per-Transcriber lock. Concurrent streams are therefore safe and
interleave at call granularity.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from livekit.agents import APIConnectOptions, APIError, stt
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer

log = logging.getLogger("service015.stt")

SAMPLE_RATE = 16000
MAX_BACKLOG_S = 10.0  # audio waiting for the worker beyond this is dropped oldest-first
MoonshineArchName = Literal["tiny", "small", "medium"]
_ARCH_ATTR = {"tiny": "TINY_STREAMING", "small": "SMALL_STREAMING", "medium": "MEDIUM_STREAMING"}
_LOCKS_GUARD = threading.Lock()


def _transcriber_lock(transcriber: Any) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = getattr(transcriber, "_service015_lock", None)
        if lock is None:
            lock = threading.Lock()
            setattr(transcriber, "_service015_lock", lock)
        return lock


def load_transcriber(language: str = "en", arch: MoonshineArchName = "small", update_interval: float = 0.5) -> Any:
    """Download (first use) and load the Moonshine model. Blocking; call it from the worker prewarm, once per process."""
    from moonshine_voice import ModelArch, Transcriber, get_model_for_language

    if arch not in _ARCH_ATTR:
        raise ValueError(f"unknown Moonshine arch {arch!r}; expected one of {', '.join(_ARCH_ATTR)}")
    model_path, model_arch = get_model_for_language(language, getattr(ModelArch, _ARCH_ATTR[arch]))
    return Transcriber(model_path=model_path, model_arch=model_arch, update_interval=update_interval)


def _base_language(tag: str) -> str:
    return tag.replace("_", "-").split("-")[0].lower()


class MoonshineSTT(stt.STT):
    def __init__(
        self, transcriber: Any, *, language: str = "en", update_interval: float = 0.5, model_name: str = "moonshine"
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=True, interim_results=True, offline_recognize=False)
        )
        self._transcriber = transcriber
        self._language = language
        self._update_interval = update_interval
        self._model_name = model_name

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def provider(self) -> str:
        return "moonshine"

    async def _recognize_impl(
        self, buffer: AudioBuffer, *, language: NotGivenOr[str] = NOT_GIVEN, conn_options: APIConnectOptions
    ) -> stt.SpeechEvent:
        raise NotImplementedError("MoonshineSTT only supports streaming recognition; use stream()")

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> MoonshineStream:
        if isinstance(language, str) and _base_language(language) != _base_language(self._language):
            raise ValueError(
                f"MoonshineSTT was loaded for language {self._language!r} and cannot transcribe {language!r}; "
                "load a transcriber for that language"
            )
        return MoonshineStream(
            stt=self,
            conn_options=conn_options,
            transcriber=self._transcriber,
            language=self._language,
            update_interval=self._update_interval,
        )


@dataclass
class _Transcript:
    kind: Literal["update", "complete"]
    text: str
    start_time: float
    duration: float


@dataclass
class _FlushDone:
    pass


@dataclass
class _Failed:
    exc: BaseException


_STOP = object()
_FLUSH = object()


class _WorkQueue:
    """Thread-safe queue of audio arrays and control items. Audio beyond `max_samples` is dropped oldest-first;
    control items (flush/stop) are never dropped."""

    def __init__(self, max_samples: int) -> None:
        self._items: collections.deque[Any] = collections.deque()
        self._cond = threading.Condition()
        self._audio_samples = 0
        self._max = max_samples
        self.dropped_samples = 0

    def put_audio(self, arr: np.ndarray) -> None:
        with self._cond:
            self._items.append(arr)
            self._audio_samples += len(arr)
            dropped = 0
            while self._audio_samples > self._max:
                for idx, it in enumerate(self._items):
                    if isinstance(it, np.ndarray):
                        del self._items[idx]
                        self._audio_samples -= len(it)
                        dropped += len(it)
                        break
                else:
                    break
            if dropped:
                first = self.dropped_samples == 0
                self.dropped_samples += dropped
                if first or (self.dropped_samples // SAMPLE_RATE) % 10 == 0:
                    log.warning(
                        "moonshine falling behind real time: dropped %.2fs of audio (%.2fs total)",
                        dropped / SAMPLE_RATE, self.dropped_samples / SAMPLE_RATE,
                    )
            self._cond.notify()

    def put_control(self, item: Any) -> None:
        with self._cond:
            self._items.append(item)
            self._cond.notify()

    def get(self) -> Any:
        with self._cond:
            while not self._items:
                self._cond.wait()
            item = self._items.popleft()
            if isinstance(item, np.ndarray):
                self._audio_samples -= len(item)
            return item


class MoonshineStream(stt.RecognizeStream):
    def __init__(
        self,
        *,
        stt: MoonshineSTT,
        conn_options: APIConnectOptions,
        transcriber: Any,
        language: str,
        update_interval: float,
    ) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=SAMPLE_RATE)
        self._transcriber = transcriber
        self._lock = _transcriber_lock(transcriber)
        self._language = language
        self._update_interval = update_interval
        self._speaking = False
        self._last_text = ""

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        inbox: asyncio.Queue[_Transcript | _FlushDone | _Failed | None] = asyncio.Queue()
        work = _WorkQueue(int(MAX_BACKLOG_S * SAMPLE_RATE))
        closed = threading.Event()

        def post(item: Any) -> None:
            # called from the worker thread; the loop may already be gone when a cancelled stream winds down
            if closed.is_set():
                return
            try:
                loop.call_soon_threadsafe(inbox.put_nowait, item)
            except RuntimeError:
                pass

        thread = threading.Thread(target=self._worker, args=(work, post, closed), name="moonshine-stt", daemon=True)
        thread.start()
        self._speaking = False
        self._last_text = ""
        feeder = asyncio.create_task(self._feed(work), name="moonshine-feed")
        try:
            while True:
                item = await inbox.get()
                if item is None:
                    break
                if isinstance(item, _Failed):
                    raise APIError(
                        f"Moonshine transcription failed: {type(item.exc).__name__}: {item.exc}", retryable=False
                    ) from item.exc
                if isinstance(item, _FlushDone):
                    self._finish_pending()
                else:
                    self._handle(item)
            self._finish_pending()
        finally:
            closed.set()
            work.put_control(_STOP)
            feeder.cancel()
            await asyncio.gather(feeder, return_exceptions=True)

    async def _feed(self, work: _WorkQueue) -> None:
        async for data in self._input_ch:
            if isinstance(data, self._FlushSentinel):
                work.put_control(_FLUSH)
                continue
            pcm = np.frombuffer(data.data, dtype=np.int16)
            if data.num_channels > 1:
                pcm = pcm.reshape(-1, data.num_channels).astype(np.float32).mean(axis=1)
            work.put_audio(pcm.astype(np.float32) / 32768.0)
        work.put_control(_STOP)

    def _worker(self, work: _WorkQueue, post: Any, closed: threading.Event) -> None:
        mstream = None
        try:
            with self._lock:
                mstream = self._transcriber.create_stream(update_interval=self._update_interval)

            def on_event(ev: Any) -> None:
                name = type(ev).__name__
                if name == "Error":
                    post(_Failed(getattr(ev, "error", None) or RuntimeError("unknown Moonshine error")))
                    return
                if name not in ("LineStarted", "LineUpdated", "LineTextChanged", "LineCompleted"):
                    return
                line = ev.line
                post(
                    _Transcript(
                        "complete" if name == "LineCompleted" else "update",
                        line.text or "",
                        float(line.start_time),
                        float(line.duration),
                    )
                )

            mstream.add_listener(on_event)
            with self._lock:
                mstream.start()
            while True:
                item = work.get()
                if item is _STOP:
                    if not closed.is_set():
                        with self._lock:
                            mstream.stop()  # transcribes the remaining audio and emits the closing events
                        post(None)
                    return
                if item is _FLUSH:
                    with self._lock:
                        mstream.stop()
                        mstream.start()
                    post(_FlushDone())
                    continue
                with self._lock:
                    mstream.add_audio(item.tolist(), SAMPLE_RATE)
        except BaseException as exc:  # surfaced to the loop as an APIError; never swallowed
            log.exception("moonshine worker failed")
            post(_Failed(exc))
        finally:
            if mstream is not None:
                try:
                    with self._lock:
                        mstream.close()  # frees the native stream on every path, cancellation included
                except Exception:
                    log.exception("moonshine stream close failed")

    def _speech_data(self, text: str, start: float, duration: float) -> list[stt.SpeechData]:
        offset = self.start_time_offset
        # Moonshine exposes no confidence score, so none is claimed (0.0 = unknown)
        return [
            stt.SpeechData(
                language=self._language, text=text, start_time=offset + start, end_time=offset + start + duration,
                confidence=0.0,
            )
        ]

    def _emit(self, type_: stt.SpeechEventType, alternatives: list[stt.SpeechData] | None = None) -> None:
        self._event_ch.send_nowait(stt.SpeechEvent(type=type_, alternatives=alternatives or []))

    def _handle(self, tr: _Transcript) -> None:
        text = tr.text.strip()
        if not text:
            return
        if not self._speaking:
            self._speaking = True
            self._emit(stt.SpeechEventType.START_OF_SPEECH)
        if tr.kind == "update":
            if text != self._last_text:
                self._last_text = text
                self._emit(stt.SpeechEventType.INTERIM_TRANSCRIPT, self._speech_data(text, tr.start_time, tr.duration))
            return
        self._last_text = ""
        self._emit(stt.SpeechEventType.FINAL_TRANSCRIPT, self._speech_data(text, tr.start_time, tr.duration))
        self._emit(stt.SpeechEventType.END_OF_SPEECH)
        self._speaking = False

    def _finish_pending(self) -> None:
        """Flush/end of input: finalize text Moonshine never completed, then close the speech segment."""
        if self._last_text:
            self._emit(stt.SpeechEventType.FINAL_TRANSCRIPT, self._speech_data(self._last_text, 0.0, 0.0))
            self._last_text = ""
        if self._speaking:
            self._emit(stt.SpeechEventType.END_OF_SPEECH)
            self._speaking = False
