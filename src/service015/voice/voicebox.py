"""Async client for the Voicebox headless backend (voice profiles only; synthesis goes through the engine sidecar).

Never logs or returns raw sample audio. Transport failures and Voicebox 5xx answers become
`DependencyUnavailable("voicebox_unavailable")`; unexpected 4xx answers become `VoiceboxRejected` (502).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from service015.errors import DependencyUnavailable, DomainError

log = logging.getLogger("service015.voice")

HEALTH_TIMEOUT_S = 2.0
UPLOAD_TIMEOUT_S = 300.0  # Voicebox validates/transcodes the sample on upload


class VoiceboxRejected(DomainError):
    """Voicebox answered with an unexpected 4xx (502)."""

    default_status = 502


def unavailable(reason: str) -> DependencyUnavailable:
    return DependencyUnavailable("voicebox_unavailable", f"Voicebox is unavailable: {reason}")


class VoiceboxClient:
    def __init__(self, base_url: str, timeout_s: float = 30.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport

    async def _request(
        self, method: str, path: str, *, timeout: float | None = None, ok_404: bool = False, **kwargs: Any
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=timeout or self._timeout) as client:
                resp = await client.request(method, self._base + path, **kwargs)
        except httpx.HTTPError as exc:
            log.warning("voicebox %s %s failed: %s", method, path, type(exc).__name__)
            raise unavailable(type(exc).__name__) from None
        if resp.status_code >= 500:
            log.warning("voicebox %s %s -> %s", method, path, resp.status_code)
            raise unavailable(f"HTTP {resp.status_code}")
        if resp.status_code == 404 and ok_404:
            return resp
        if resp.status_code >= 400:
            log.warning("voicebox %s %s rejected: %s", method, path, resp.status_code)
            raise VoiceboxRejected(
                "voicebox_rejected",
                "Voicebox rejected the request",
                details={"upstream_status": resp.status_code, "upstream_detail": _detail(resp)},
            )
        return resp

    async def health(self) -> str:
        """'ok' or 'unavailable'; never raises."""
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=HEALTH_TIMEOUT_S) as client:
                resp = await client.get(self._base + "/health")
        except (httpx.HTTPError, httpx.InvalidURL):
            return "unavailable"
        return "ok" if resp.status_code == 200 else "unavailable"

    async def list_profiles(self) -> list[dict[str, Any]]:
        return _json(await self._request("GET", "/profiles"))

    async def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        """The profile, or None when Voicebox does not know it (its sample list answers 200 [] for unknown ids)."""
        if not _PROFILE_ID.fullmatch(profile_id or ""):
            return None
        resp = await self._request("GET", f"/profiles/{profile_id}", ok_404=True)
        return None if resp.status_code == 404 else _json(resp)

    async def create_cloned_profile(
        self,
        name: str,
        language: str,
        description: str,
        sample_bytes: bytes,
        filename: str,
        reference_text: str,
    ) -> dict[str, Any]:
        """Create a cloned Turbo profile and upload its reference sample. A failed upload deletes the new profile."""
        resp = await self._request(
            "POST",
            "/profiles",
            json={
                "name": name,
                "description": description,
                "language": language,
                "voice_type": "cloned",
                "default_engine": "chatterbox_turbo",
            },
        )
        profile = _json(resp)
        try:
            await self._request(
                "POST",
                f"/profiles/{profile['id']}/samples",
                timeout=UPLOAD_TIMEOUT_S,
                files={"file": (filename, sample_bytes, "application/octet-stream")},
                data={"reference_text": reference_text},
            )
        except Exception:
            await self.delete_profile(profile["id"])
            raise
        return profile

    async def delete_profile(self, profile_id: str) -> None:
        """Best-effort cleanup of a profile we just created; failure is logged, not raised."""
        try:
            await self._request("DELETE", f"/profiles/{profile_id}", ok_404=True)
        except DomainError:
            log.error("could not delete voicebox profile %s after a failed create; remove it manually", profile_id)


_PROFILE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        raise VoiceboxRejected("voicebox_bad_response", "Voicebox returned a non-JSON body") from None


def _detail(resp: httpx.Response) -> Any:
    try:
        return resp.json().get("detail")
    except (ValueError, AttributeError):
        return None
