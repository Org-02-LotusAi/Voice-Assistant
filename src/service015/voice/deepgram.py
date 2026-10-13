"""Deepgram Aura-2 voice catalog, voice-id namespace, and the REST client used for voice previews.

A persona references a Deepgram voice as `deepgram:<model>` (for example `deepgram:aura-2-thalia-en`) in the same
`voice.profile_id` field that holds a Voicebox profile id in local mode. The `deepgram:` prefix is the namespace: a local profile
id never starts with it, so one field serves both providers and a mismatch is detectable from the id alone.

The catalog is curated from the official table at https://developers.deepgram.com/docs/tts-models (English Aura-2 voices,
checked 2026-10-13). Cloned voices do not exist for this provider.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from service015.errors import DependencyUnavailable, DomainError

log = logging.getLogger("service015.voice")

NAMESPACE = "deepgram:"
ENGINE = "deepgram-aura-2"
SPEAK_URL = "https://api.deepgram.com/v1/speak"
PREVIEW_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class AuraVoice:
    model: str  # Deepgram model name, e.g. aura-2-thalia-en
    gender: str  # feminine | masculine
    accent: str  # American | British | Australian | Filipino

    @property
    def voice_id(self) -> str:
        return NAMESPACE + self.model

    @property
    def display_name(self) -> str:
        name = self.model.removeprefix("aura-2-").removesuffix("-en").capitalize()
        return f"{name} ({self.gender}, {self.accent})"


def _v(name: str, gender: str, accent: str = "American") -> AuraVoice:
    return AuraVoice(f"aura-2-{name}-en", gender, accent)


AURA_2_ENGLISH: tuple[AuraVoice, ...] = (
    _v("thalia", "feminine"), _v("amalthea", "feminine", "Filipino"), _v("andromeda", "feminine"), _v("apollo", "masculine"),
    _v("arcas", "masculine"), _v("aries", "masculine"), _v("asteria", "feminine"), _v("athena", "feminine"),
    _v("atlas", "masculine"), _v("aurora", "feminine"), _v("callista", "feminine"), _v("cora", "feminine"),
    _v("cordelia", "feminine"), _v("delia", "feminine"), _v("draco", "masculine", "British"), _v("electra", "feminine"),
    _v("harmonia", "feminine"), _v("helena", "feminine"), _v("hera", "feminine"), _v("hermes", "masculine"),
    _v("hyperion", "masculine", "Australian"), _v("iris", "feminine"), _v("janus", "feminine"), _v("juno", "feminine"),
    _v("jupiter", "masculine"), _v("luna", "feminine"), _v("mars", "masculine"), _v("minerva", "feminine"),
    _v("neptune", "masculine"), _v("odysseus", "masculine"), _v("ophelia", "feminine"), _v("orion", "masculine"),
    _v("orpheus", "masculine"), _v("pandora", "feminine", "British"), _v("phoebe", "feminine"), _v("pluto", "masculine"),
    _v("saturn", "masculine"), _v("selene", "feminine"), _v("theia", "feminine", "Australian"), _v("vesta", "feminine"),
    _v("zeus", "masculine"),
)
_BY_ID = {v.voice_id: v for v in AURA_2_ENGLISH}


def is_deepgram_voice_id(voice_id: str) -> bool:
    return voice_id.startswith(NAMESPACE)


def find_voice(voice_id: str) -> AuraVoice | None:
    return _BY_ID.get(voice_id)


def model_of(voice_id: str) -> str:
    """The Deepgram model name for a catalog voice id. Raises KeyError for an id outside the catalog."""
    return _BY_ID[voice_id].model


class DeepgramSpeakClient:
    """POST /v1/speak (REST) -> a complete WAV, for voice previews. The key is only ever sent in the Authorization header."""

    def __init__(self, api_key: str, *, url: str = SPEAK_URL, transport: httpx.AsyncBaseTransport | None = None,
                 timeout_s: float = PREVIEW_TIMEOUT_S) -> None:
        self._key = api_key
        self._url = url
        self._transport = transport
        self._timeout_s = timeout_s

    async def preview(self, voice_id: str, text: str) -> bytes:
        voice = find_voice(voice_id)
        if voice is None:
            raise DomainError("voice_profile_not_found", f"unknown Deepgram voice {voice_id!r}", 422, {"voice_profile_id": voice_id})
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout_s) as client:
                resp = await client.post(
                    self._url,
                    params={"model": voice.model, "encoding": "linear16", "container": "wav", "sample_rate": "24000"},
                    headers={"Authorization": f"Token {self._key}", "Content-Type": "application/json"},
                    json={"text": text},
                )
        except httpx.HTTPError as exc:
            raise DependencyUnavailable(
                "deepgram_unavailable", f"Deepgram text-to-speech is unreachable: {type(exc).__name__}",
                details={"dependency": "deepgram"},
            ) from None
        if resp.status_code in (401, 403):
            raise DependencyUnavailable(
                "deepgram_unavailable", f"Deepgram rejected the API key (HTTP {resp.status_code}); check DEEPGRAM_API_KEY",
                details={"dependency": "deepgram", "upstream_status": resp.status_code},
            )
        if resp.status_code != 200 or not resp.content:
            log.warning("deepgram preview failed: HTTP %s", resp.status_code)
            raise DependencyUnavailable(
                "deepgram_unavailable", f"Deepgram text-to-speech answered HTTP {resp.status_code}",
                details={"dependency": "deepgram", "upstream_status": resp.status_code},
            )
        return resp.content
