"""Adapter to Service 014's telephony primitive.

Service 015 reaches a phone only through Service 014: either its HTTP API (assumed contract, see
`HttpBackend`) or the LiveKit SIP primitive on the same LiveKit deployment. The phone number is
never logged in full (`mask_number`).
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import string
from typing import Any, Callable, Protocol

import aiohttp
import httpx
from livekit import api
from livekit.api.twirp_client import ServerError

from service015.config import Settings
from service015.errors import DependencyUnavailable, DomainError
from service015.resolver import ResolvedIdentity

log = logging.getLogger("service015.telephony")

E164 = re.compile(r"^\+[1-9][0-9]{1,14}$")
HTTP_TIMEOUT_S = 60.0  # the call can ring for a while before Service 014 answers the POST
# livekit_sip: the dial request blocks until the callee answers or this ringing timeout expires (then 502);
# the HTTP client timeout leaves headroom for SIP signalling on top of it.
RINGING_TIMEOUT_S = 45
SIP_REQUEST_TIMEOUT_S = RINGING_TIMEOUT_S + 15.0
_SUFFIX_ALPHABET = string.ascii_lowercase + string.digits


def caller_identity(to: str) -> str:
    """Unique LiveKit participant identity per call: `phone-<last4>-<rand4>` (two numbers sharing the last
    four digits, or a redial, must not collide inside the same room)."""
    return f"phone-{to[-4:]}-{''.join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(4))}"


class TelephonyFailed(DomainError):
    """Service 014 / the SIP layer rejected or failed the call (502)."""

    default_status = 502


def mask_number(number: str) -> str:
    """Keep only the last 4 characters."""
    return "*" * max(len(number) - 4, 0) + number[-4:]


def not_configured(reason: str) -> DependencyUnavailable:
    return DependencyUnavailable("telephony_not_configured", reason)


class Backend(Protocol):
    async def dial(self, room: str, to: str) -> dict[str, str]: ...


class HttpBackend:
    """ASSUMED Service 014 HTTP contract (the real one is not known yet; adjust only this class).

    POST {SERVICE014_URL}/calls  Authorization: Bearer <SERVICE014_API_KEY>  json {"room": str, "to": E.164}
    -> 2xx json {"call_id": str, "status"?: str}
    """

    def __init__(self, base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not api_key:
            raise ValueError("HttpBackend requires a non-empty API key")
        self._url = base_url.rstrip("/") + "/calls"
        self._api_key = api_key
        self._transport = transport

    async def dial(self, room: str, to: str) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=HTTP_TIMEOUT_S) as client:
                resp = await client.post(self._url, json={"room": room, "to": to}, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("service014 http dial failed: %s to=%s", type(exc).__name__, mask_number(to))
            raise TelephonyFailed("telephony_failed", "Service 014 is unreachable", details={"mode": "http"}) from None
        if resp.status_code >= 300:
            log.warning("service014 http dial rejected: status=%s to=%s", resp.status_code, mask_number(to))
            raise TelephonyFailed(
                "telephony_failed", "Service 014 rejected the call", details={"mode": "http", "upstream_status": resp.status_code}
            )
        try:
            body = resp.json()
            call_id = body["call_id"]
        except (ValueError, KeyError, TypeError):
            raise TelephonyFailed(
                "telephony_bad_response", "Service 014 response lacks a call_id", details={"mode": "http"}
            ) from None
        return {"call_id": str(call_id), "status": str(body.get("status", "dialing"))}


class LiveKitSipBackend:
    """Service 014's SIP primitive on the shared LiveKit deployment (CreateSIPParticipant)."""

    def __init__(
        self, url: str, api_key: str, api_secret: str, trunk_id: str, api_factory: Callable[[], Any] | None = None
    ) -> None:
        self._trunk_id = trunk_id
        self._factory = api_factory or (lambda: api.LiveKitAPI(url, api_key, api_secret))

    async def dial(self, room: str, to: str) -> dict[str, str]:
        request = api.CreateSIPParticipantRequest(
            sip_trunk_id=self._trunk_id,
            sip_call_to=to,
            room_name=room,
            participant_identity=caller_identity(to),
            wait_until_answered=True,
            ringing_timeout={"seconds": RINGING_TIMEOUT_S},
        )
        lk = self._factory()
        try:
            info = await lk.sip.create_sip_participant(request, timeout=SIP_REQUEST_TIMEOUT_S)
        except (ServerError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("livekit sip dial failed: %s to=%s", type(exc).__name__, mask_number(to))
            raise TelephonyFailed(
                "telephony_failed", "the SIP call could not be completed", details={"mode": "livekit_sip"}
            ) from None
        finally:
            await lk.aclose()
        return {"call_id": info.sip_call_id or info.participant_id, "status": "answered"}


class Service014Client:
    def __init__(self, mode: str, backend: Backend | None = None, config_error: str | None = None) -> None:
        self.mode = mode
        self._backend = backend
        self._config_error = config_error

    @classmethod
    def from_settings(cls, s: Settings) -> "Service014Client":
        mode = s.service014_mode
        if mode == "http":
            if not s.service014_url:
                return cls(mode, config_error="SERVICE014_URL is not set")
            if not s.service014_api_key:
                return cls(mode, config_error="SERVICE014_API_KEY is not set (required in http mode)")
            return cls(mode, HttpBackend(s.service014_url, s.service014_api_key))
        if mode == "livekit_sip":
            if not s.service014_sip_trunk_id:
                return cls(mode, config_error="SERVICE014_SIP_TRUNK_ID is not set")
            return cls(
                mode, LiveKitSipBackend(s.livekit_url, s.livekit_api_key, s.livekit_api_secret, s.service014_sip_trunk_id)
            )
        return cls("disabled")

    def ensure_configured(self) -> Backend:
        if self.mode == "disabled":
            raise not_configured("telephony is disabled (SERVICE014_MODE=disabled)")
        if self._config_error or self._backend is None:
            raise not_configured(self._config_error or "telephony backend is not configured")
        return self._backend

    async def connect_phone(self, identity: ResolvedIdentity, to_number: str) -> dict[str, str]:
        """Dial `to_number` into the Identity's Default LiveKit Room. Returns {call_id, room, status}."""
        backend = self.ensure_configured()
        if not E164.match(to_number):
            raise DomainError("invalid_phone_number", "number must be E.164, e.g. +919876543210", status=422)
        room = identity.participant.default_room
        result = await backend.dial(room, to_number)
        log.info("phone call placed identity=%s room=%s to=%s", identity.identity_id, room, mask_number(to_number))
        return {"call_id": result["call_id"], "room": room, "status": result["status"]}

    def inbound_route(self, identity: ResolvedIdentity) -> dict[str, str] | None:
        """How inbound calls reach this Identity, or None when not available in the current mode."""
        if self.mode != "livekit_sip":
            return None
        return {"kind": "SIPDispatchRuleDirect", "room_name": identity.participant.default_room}
