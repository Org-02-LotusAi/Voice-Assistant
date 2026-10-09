"""Async client for the voice engine sidecar (engine/, Chatterbox Turbo on :17494).

Sidecar errors are `{"code", "detail"}` JSON; they are mapped to DomainErrors here. Audio is never logged.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

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
        request_id: str | None = None
        expected_chunks: int | None = None
        read_error: str | None = None
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
                async with client.stream(
                    "POST", self._base + "/synthesize/stream", json={"profile_id": profile_id, "text": text}
                ) as resp:
                    if resp.status_code != 200:
                        raise _map_error(resp.status_code, await resp.aread())
                    request_id = resp.headers.get("x-request-id")
                    header = resp.headers.get("x-chunk-count", "")
                    expected_chunks = int(header) if header.isdigit() else None
                    try:
                        async for chunk in resp.aiter_bytes():
                            if chunk:
                                yield chunk
                    except httpx.HTTPError as exc:
                        read_error = type(exc).__name__
                        log.warning("voice engine stream broke mid-body: %s", read_error)
                if request_id is None:
                    raise VoiceEngineFailed(
                        "tts_failed", "the voice engine sent no X-Request-Id, so the stream cannot be verified"
                    )
                await self._verify_complete(client, request_id, expected_chunks, read_error)
        except httpx.HTTPError as exc:
            log.warning("voice engine stream failed: %s", type(exc).__name__)
            raise engine_unavailable(type(exc).__name__) from None

    async def _verify_complete(
        self, client: httpx.AsyncClient, request_id: str, expected_chunks: int | None, read_error: str | None
    ) -> None:
        resp = await client.get(self._base + f"/requests/{request_id}")
        if resp.status_code != 200:
            log.warning("voice engine request status %s unavailable: HTTP %s", request_id, resp.status_code)
            raise VoiceEngineFailed(
                "tts_failed",
                "could not verify that the synthesized stream was complete",
                details={"request_id": request_id, "read_error": read_error, "status_lookup": resp.status_code},
            )
        st = resp.json()
        state, code = st.get("state"), str(st.get("code") or "")
        done, total = st.get("chunks_done"), st.get("chunks_total")
        details = {"request_id": request_id, "state": state, "chunks_done": done, "chunks_total": total,
                   "read_error": read_error}
        if state == "complete" and read_error is None and done == total and (expected_chunks in (None, total)):
            return
        if state == "failed" and code:
            err = _from_engine_code(code, str(st.get("detail") or ""))
            err.details.update(details)
            raise err
        raise VoiceEngineFailed(
            "tts_failed", f"the synthesized stream is incomplete (state={state})", details=details
        )
