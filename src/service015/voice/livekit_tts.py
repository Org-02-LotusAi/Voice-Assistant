"""LiveKit Agents TTS plugin that streams PCM from the voice engine sidecar (engine/, POST /synthesize/stream).

The sidecar answers 200 + `audio/pcm` (s16le mono 24 kHz) and sends each chunk as soon as it is generated. Once the 200 is out an error
cannot change the status, so a failed stream is detected as engine/README.md describes: an abnormal end of the HTTP read, and the
`GET /requests/{X-Request-Id}` state. A truncated or failed stream always raises a framework API error; it never ends silently.
Cancellation (barge-in) leaves the `async with` block, which closes the HTTP response immediately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable

import httpx
from livekit.agents import APIConnectionError, APIConnectOptions, APIError, APIStatusError, APITimeoutError, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

log = logging.getLogger("service015.voice")

SAMPLE_RATE = 24000
NUM_CHANNELS = 1
# After the byte stream ends the request may still read "streaming" for a moment; wait this long for a terminal state.
STATUS_SETTLE_S = 3.0
STATUS_POLL_S = 0.1
# Failure codes (engine/README.md) that a retry cannot fix.
_PERMANENT_CODES = {"reference_too_short", "corrupt_reference", "profile_not_found"}

FirstAudioCallback = Callable[[float], None]


class ChatterboxTTS(tts.TTS):
    """Non-streaming-input TTS (the framework wraps it in a sentence adapter); the audio output is streamed."""

    def __init__(
        self,
        *,
        base_url: str,
        profile_id: str,
        read_timeout_s: float = 120.0,
        on_first_audio: FirstAudioCallback | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False), sample_rate=SAMPLE_RATE, num_channels=NUM_CHANNELS
        )
        self._base = base_url.rstrip("/")
        self._profile_id = profile_id
        self._read_timeout_s = read_timeout_s
        self.on_first_audio = on_first_audio
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def model(self) -> str:
        return "chatterbox-turbo"

    @property
    def provider(self) -> str:
        return "chatterbox"

    @property
    def profile_id(self) -> str:
        return self._profile_id

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(transport=self._transport)
        return self._client

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChatterboxChunkedStream:
        return ChatterboxChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class ChatterboxChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: ChatterboxTTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._engine: ChatterboxTTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        eng = self._engine
        timeout = httpx.Timeout(
            connect=self._conn_options.timeout, read=eng._read_timeout_s, write=self._conn_options.timeout,
            pool=self._conn_options.timeout,
        )
        request_id = ""
        total = 0
        carry = b""
        first = True
        try:
            # leaving this block (normally, on error, or on CancelledError from a barge-in) closes the response
            async with eng._http().stream(
                "POST", eng._base + "/synthesize/stream",
                json={"profile_id": eng._profile_id, "text": self._input_text}, timeout=timeout,
            ) as resp:
                request_id = resp.headers.get("x-request-id", "")
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise _status_error(resp.status_code, body, request_id)
                sample_rate = resp.headers.get("x-sample-rate", str(SAMPLE_RATE))
                if sample_rate != str(SAMPLE_RATE):
                    raise APIError(
                        f"voice engine sample rate {sample_rate} does not match the expected {SAMPLE_RATE}", retryable=False
                    )
                output_emitter.initialize(
                    request_id=request_id or "unknown", sample_rate=SAMPLE_RATE, num_channels=NUM_CHANNELS,
                    mime_type="audio/pcm",
                )
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    data, carry = _whole_samples(carry + chunk)
                    if not data:
                        continue
                    if first:
                        first = False
                        self._notify_first_audio()
                    total += len(data)
                    output_emitter.push(data)
        except httpx.TimeoutException as exc:
            raise APITimeoutError(f"voice engine timed out: {type(exc).__name__}") from exc
        except httpx.HTTPError as exc:
            # connect failures, and a response that ends abnormally (RemoteProtocolError / ReadError) = truncated stream
            raise APIConnectionError(f"voice engine stream failed: {type(exc).__name__}: {exc}") from exc

        if carry:
            raise APIError("voice engine stream ended mid-sample (truncated)", retryable=True)
        await self._verify_complete(request_id, total)
        output_emitter.flush()

    def _notify_first_audio(self) -> None:
        cb = self._engine.on_first_audio
        if cb is None:
            return
        try:
            cb(time.monotonic())
        except Exception:
            # a telemetry hook must not kill speech, but it must not vanish either
            log.exception("on_first_audio callback raised")

    async def _verify_complete(self, request_id: str, total_bytes: int) -> None:
        if not request_id:
            raise APIError("voice engine response carried no X-Request-Id; cannot verify the stream completed", retryable=False)
        eng = self._engine
        deadline = time.monotonic() + STATUS_SETTLE_S
        while True:
            try:
                resp = await eng._http().get(
                    f"{eng._base}/requests/{request_id}", timeout=httpx.Timeout(self._conn_options.timeout)
                )
            except httpx.TimeoutException as exc:
                raise APITimeoutError("voice engine request-status check timed out") from exc
            except httpx.HTTPError as exc:
                raise APIConnectionError(f"voice engine request-status check failed: {type(exc).__name__}") from exc
            if resp.status_code != 200:
                raise _status_error(resp.status_code, resp.content, request_id)
            info = resp.json()
            state = info.get("state")
            if state == "complete":
                if total_bytes == 0:
                    raise APIError("voice engine reported complete but sent no audio", retryable=True)
                return
            if state == "streaming" and time.monotonic() < deadline:
                await asyncio.sleep(STATUS_POLL_S)
                continue
            code = info.get("code") or state
            raise APIError(
                f"voice engine stream did not complete: state={state} code={code} detail={info.get('detail')} "
                f"chunks={info.get('chunks_done')}/{info.get('chunks_total')}",
                body=info,
                retryable=code not in _PERMANENT_CODES and state != "cancelled",
            )


def _whole_samples(buf: bytes) -> tuple[bytes, bytes]:
    """Split off a trailing odd byte so every push is whole s16le samples."""
    n = len(buf) - (len(buf) % 2)
    return buf[:n], buf[n:]


def _status_error(status: int, body: bytes, request_id: str) -> APIStatusError:
    code = detail = ""
    parsed: object = None
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            code, detail = str(parsed.get("code", "")), str(parsed.get("detail", ""))
    except ValueError:
        pass
    return APIStatusError(
        f"voice engine returned {status} {code or 'error'}: {detail}".rstrip(": "),
        status_code=status, request_id=request_id or None, body=parsed,
    )
