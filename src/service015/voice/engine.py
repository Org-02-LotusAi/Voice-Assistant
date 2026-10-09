"""Async client for the voice engine sidecar (engine/, Chatterbox Turbo on :17494).

Sidecar errors are `{"code", "detail"}` JSON; they are mapped to DomainErrors here. Audio is never logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

import httpx

from service015.errors import DependencyUnavailable, DomainError, NotFound, ValidationFailed

log = logging.getLogger("service015.voice")

HEALTH_TIMEOUT_S = 2.0


class VoiceEngineFailed(DomainError):
    """The sidecar answered with an unexpected error (502)."""

    default_status = 502


def engine_unavailable(reason: str) -> DependencyUnavailable:
    return DependencyUnavailable("voice_engine_unavailable", f"the voice engine is unavailable: {reason}")


def _map_error(status: int, body: bytes) -> DomainError:
    code, detail = "", ""
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            code, detail = str(parsed.get("code", "")), str(parsed.get("detail", ""))
    except ValueError:
        pass
    if status == 404 and code == "profile_not_found":
        return NotFound("voice_profile_not_found", "the voice engine does not know this voice profile")
    if status == 422:
        return ValidationFailed(code or "voice_engine_rejected", detail or "the voice engine rejected the request")
    if status == 503 and code == "voicebox_unavailable":
        return DependencyUnavailable("voicebox_unavailable", "Voicebox is unavailable: reported by the voice engine")
    if status == 503:
        return DependencyUnavailable(
            "voice_engine_unavailable", f"the voice engine is not ready ({code or 'unavailable'})", details={"engine_code": code}
        )
    return VoiceEngineFailed(
        "voice_engine_error", "the voice engine failed", details={"upstream_status": status, "engine_code": code}
    )


def _from_engine_code(code: str, detail: str) -> DomainError:
    """Map an engine failure code (HTTP error body or /requests status) to a DomainError carrying that code."""
    if code == "profile_not_found":
        return NotFound("voice_profile_not_found", "the voice engine does not know this voice profile")
    if code == "voicebox_unavailable":
        return DependencyUnavailable("voicebox_unavailable", "Voicebox is unavailable: reported by the voice engine")
    if code in ("reference_too_short", "corrupt_reference", "invalid_request", "empty_text"):
        return ValidationFailed(code, detail or "the voice engine rejected the request")
    return VoiceEngineFailed(code, detail or "the voice engine failed")


class VoiceEngineClient:
    def __init__(self, base_url: str, timeout_s: float = 120.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport

    async def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
                resp = await client.post(self._base + path, json=payload)
        except httpx.HTTPError as exc:
            log.warning("voice engine POST %s failed: %s", path, type(exc).__name__)
            raise engine_unavailable(type(exc).__name__) from None
        if resp.status_code != 200:
            raise _map_error(resp.status_code, resp.content)
        return resp

    async def health(self) -> str:
        """The sidecar's own status ('ok' | 'loading' | 'error') or 'unavailable'; never raises."""
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=HEALTH_TIMEOUT_S) as client:
                resp = await client.get(self._base + "/health")
            status = str(resp.json().get("status", "error"))
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, AttributeError):
            return "unavailable"
        return status if status in ("ok", "loading", "error") else "error"

    async def warm(self, profile_id: str) -> dict[str, Any]:
        return (await self._post("/warm", {"profile_id": profile_id})).json()

    async def preview(self, profile_id: str, text: str) -> bytes:
        """One complete WAV."""
        return (await self._post("/preview", {"profile_id": profile_id, "text": text})).content


    async def stream(self, profile_id: str, text: str) -> AsyncIterator[bytes]:
        """Yield s16le mono 24 kHz PCM chunks as the sidecar produces them.

        The sidecar cannot change the HTTP status once streaming began, so after EOF (or a broken read) the
        request status (`GET /requests/{id}`) is the authority: anything but `complete` with all chunks done
        raises a coded error (`tts_failed`, or the engine's own code such as `reference_too_short`).
        """
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
                async with open_engine_stream(client, self._base, profile_id, text) as es:
                    async for chunk in es.chunks():
                        yield chunk
                    await es.verify()
        except EngineStreamError as exc:
            raise _domain_error(exc) from None


def _domain_error(exc: EngineStreamError) -> DomainError:
    if exc.kind in ("connection", "timeout"):
        log.warning("voice engine stream failed: %s", exc.detail)
        return engine_unavailable(exc.detail)
    if exc.kind == "http_status":
        return _map_error(exc.status or 0, exc.body)
    if exc.kind == "failed":
        err = _from_engine_code(exc.code, exc.detail)
        err.details.update(exc.details)
        return err
    return VoiceEngineFailed("tts_failed", exc.detail, details=exc.details)


# ---------------------------------------------------------------------------------------------------------------
# Transport-neutral core of the /synthesize/stream protocol (engine/README.md "Detecting a failed or truncated
# stream"). Both VoiceEngineClient.stream (DomainError) and the LiveKit TTS plugin (APIError) use it; each maps
# EngineStreamError to its own error vocabulary. Use it with `async with` so that cancellation closes the response.
# ---------------------------------------------------------------------------------------------------------------

SAMPLE_RATE = 24000
STATUS_SETTLE_S = 3.0  # how long to wait for a request to leave state=streaming after the body ended
STATUS_POLL_S = 0.1
_PERMANENT_CODES = frozenset({"reference_too_short", "corrupt_reference", "profile_not_found", "invalid_request", "empty_text"})


class EngineStreamError(Exception):
    """Neutral stream failure. kind: connection | timeout | http_status | failed | incomplete | protocol."""

    def __init__(
        self, kind: str, code: str, detail: str = "", *, status: int | None = None, body: bytes = b"",
        retryable: bool = False, request_id: str | None = None, details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{kind}:{code}: {detail}")
        self.kind, self.code, self.detail = kind, code, detail
        self.status, self.body, self.retryable, self.request_id = status, body, retryable, request_id
        self.details = dict(details or {})


class EngineStream:
    """An open synthesis response. Iterate `chunks()` to the end, then `await verify()`."""

    def __init__(
        self, client: httpx.AsyncClient, base: str, request_id: str | None, expected_chunks: int | None,
        response: httpx.Response, on_first_byte: Callable[[], None] | None,
    ) -> None:
        self._client, self._base, self._response = client, base, response
        self.request_id = request_id
        self.expected_chunks = expected_chunks
        self.bytes_received = 0
        self.read_error: str | None = None
        self.read_error_kind: str | None = None
        self._carry = b""
        self._on_first_byte = on_first_byte

    async def chunks(self) -> AsyncIterator[bytes]:
        """Whole-sample (even length) PCM chunks. A body that breaks mid-way ends the iteration; `verify()` then raises."""
        try:
            async for raw in self._response.aiter_bytes():
                if not raw:
                    continue
                if self.bytes_received == 0 and self._on_first_byte is not None:
                    self._on_first_byte()  # T3: first PCM byte from the engine, before any framing/buffering
                self.bytes_received += len(raw)
                buf = self._carry + raw
                n = len(buf) - (len(buf) % 2)
                self._carry = buf[n:]
                if n:
                    yield buf[:n]
        except httpx.HTTPError as exc:
            self.read_error = type(exc).__name__
            self.read_error_kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "connection"
            log.warning("voice engine stream broke mid-body: %s", self.read_error)

    async def verify(self) -> None:
        """The request status is the authority on completion; raise EngineStreamError unless it is complete."""
        rid = self.request_id
        if rid is None:
            raise EngineStreamError(
                "protocol", "tts_failed", "the voice engine sent no X-Request-Id, so the stream cannot be verified"
            )
        deadline = time.monotonic() + STATUS_SETTLE_S
        while True:
            try:
                resp = await self._client.get(self._base + f"/requests/{rid}")
            except httpx.HTTPError as exc:
                kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "connection"
                raise EngineStreamError(kind, "engine_unavailable", type(exc).__name__, retryable=True, request_id=rid) from None
            if resp.status_code != 200:
                log.warning("voice engine request status %s unavailable: HTTP %s", rid, resp.status_code)
                raise EngineStreamError(
                    "incomplete", "tts_failed", "could not verify that the synthesized stream was complete",
                    request_id=rid,
                    details={"request_id": rid, "read_error": self.read_error, "status_lookup": resp.status_code},
                )
            st = resp.json()
            if st.get("state") == "streaming" and time.monotonic() < deadline:
                await asyncio.sleep(STATUS_POLL_S)
                continue
            break
        state, code = st.get("state"), str(st.get("code") or "")
        done, total = st.get("chunks_done"), st.get("chunks_total")
        details = {"request_id": rid, "state": state, "chunks_done": done, "chunks_total": total,
                   "read_error": self.read_error}
        if (state == "complete" and self.read_error is None and done == total
                and self.expected_chunks in (None, total) and not self._carry and self.bytes_received > 0):
            return
        if state == "failed" and code:
            raise EngineStreamError(
                "failed", code, str(st.get("detail") or ""), retryable=code not in _PERMANENT_CODES, request_id=rid,
                details=details,
            )
        raise EngineStreamError(
            self.read_error_kind or "incomplete", "tts_failed", f"the synthesized stream is incomplete (state={state})",
            retryable=state != "cancelled", request_id=rid, details=details,
        )


@asynccontextmanager
async def open_engine_stream(
    client: httpx.AsyncClient, base: str, profile_id: str, text: str, *, timeout: httpx.Timeout | float | None = None,
    on_first_byte: Callable[[], None] | None = None,
) -> AsyncIterator[EngineStream]:
    """POST /synthesize/stream and validate the response head. Leaving the block (also on CancelledError) closes the response."""
    kwargs: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
    base = base.rstrip("/")
    try:
        async with client.stream(
            "POST", base + "/synthesize/stream", json={"profile_id": profile_id, "text": text}, **kwargs
        ) as resp:
            request_id = resp.headers.get("x-request-id")
            if resp.status_code != 200:
                body = await resp.aread()
                code = ""
                try:
                    parsed = json.loads(body)
                    code = str(parsed.get("code", "")) if isinstance(parsed, dict) else ""
                except ValueError:
                    pass
                raise EngineStreamError(
                    "http_status", code or "http_error", f"HTTP {resp.status_code}", status=resp.status_code, body=body,
                    retryable=resp.status_code >= 500 and code not in _PERMANENT_CODES, request_id=request_id,
                )
            rate = resp.headers.get("x-sample-rate", str(SAMPLE_RATE))
            if rate != str(SAMPLE_RATE):
                raise EngineStreamError(
                    "protocol", "tts_failed",
                    f"voice engine sample rate {rate} does not match the expected {SAMPLE_RATE}", request_id=request_id,
                )
            header = resp.headers.get("x-chunk-count", "")
            yield EngineStream(client, base, request_id, int(header) if header.isdigit() else None, resp, on_first_byte)
    except httpx.TimeoutException as exc:
        raise EngineStreamError("timeout", "engine_unavailable", type(exc).__name__, retryable=True) from None
    except httpx.HTTPError as exc:
        raise EngineStreamError("connection", "engine_unavailable", type(exc).__name__, retryable=True) from None
