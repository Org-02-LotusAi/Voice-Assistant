"""API-only request/response models (mirror components/schemas in openapi.yaml)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Health(_Strict):
    status: Literal["ok", "degraded"]
    version: str
    dependencies: dict[str, str]


class InstantiateRequest(_Strict):
    overrides: dict[str, Any] = Field(default_factory=dict)
    template_version: int | None = Field(default=None, ge=1)


class PatchIdentityRequest(_Strict):
    overrides: dict[str, Any]


class IdentitySummary(_Strict):
    identity_id: str
    name: str
    template_id: str
    template_version: int
    company: str | None
    role: str | None
    language: str
    voice_profile_id: str | None
    participant_identity: str
    default_room: str
    resolved_hash: str


class SelfContext(_Strict):
    self_context: str
    instructions: str
    resolved_hash: str


class Capabilities(_Strict):
    audio_receive: bool
    audio_publish: bool
    barge_in: bool
    video_publish: bool


class ParticipantStatus(_Strict):
    identity_id: str
    participant_identity: str
    default_room: str
    presence: Literal["READY", "STARTING", "BUSY", "ERROR", "OFFLINE"]
    status_detail: str | None
    last_error: str | None
    capabilities: Capabilities
    launched_at: str | None
    heartbeat_at: str | None
