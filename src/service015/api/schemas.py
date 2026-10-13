"""API-only request/response models (mirror components/schemas in openapi.yaml)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthProviders(_Strict):
    stt: Literal["local", "deepgram"]
    tts: Literal["local", "deepgram"]


class Health(_Strict):
    status: Literal["ok", "degraded"]
    version: str
    dependencies: dict[str, str]
    providers: HealthProviders


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
    owner_user_id: str | None = None


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


class DialRequest(_Strict):
    to: str = Field(pattern=r"^\+[1-9][0-9]{1,14}$")


class PhoneCall(_Strict):
    call_id: str
    room: str
    status: str


class InboundRoute(_Strict):
    kind: str
    room_name: str


class PhoneInfo(_Strict):
    mode: Literal["disabled", "http", "livekit_sip"]
    default_room: str
    inbound: InboundRoute | None


class ExperienceCreate(_Strict):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8000)
    source: str = Field(min_length=1, max_length=500)
    repo: str | None = Field(default=None, min_length=1, max_length=500)
    branch: str | None = Field(default=None, min_length=1, max_length=200)
    sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{7,40}$")
    min_role: Literal["viewer", "operator", "admin"] = "viewer"


class ExpertKnowledgeItem(_Strict):
    id: int
    kind: Literal["document", "verified_experience"]
    title: str
    source: str
    repo: str
    branch: str
    sha: str
    min_role: Literal["viewer", "operator", "admin"]
    created_at: str
    dirty: bool


class ExpertKnowledgeList(_Strict):
    items: list[ExpertKnowledgeItem]
    missing_required: list[str]


class ExpertSession(_Strict):
    session_id: str


class AskRequest(_Strict):
    question: str = Field(min_length=1, max_length=2000)


class Citation(_Strict):
    id: int
    kind: Literal["document", "verified_experience"]
    title: str
    source: str
    repo: str
    branch: str
    sha: str


class AskResponse(_Strict):
    answer: str
    citations: list[Citation]


class VoiceProfile(_Strict):
    voice_profile_id: str
    name: str
    language: str
    engine: Literal["chatterbox-turbo", "deepgram-aura-2"]
    authorized: bool
    shared: bool = False  # unowned or admin-owned (usable by everyone)
    owned_by_me: bool = False  # uploaded by the calling account (false for tokens / env keys)


class VoiceAuthorizationCreate(_Strict):
    consent_statement: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    subject_name: str = Field(min_length=1, max_length=200, pattern=r"\S")
    authorized_by: str = Field(min_length=1, max_length=200, pattern=r"\S")
    source: str = Field(default="voicebox_direct", min_length=1, max_length=200, pattern=r"\S")


class VoiceAuthorization(_Strict):
    id: int
    voice_profile_id: str
    authorized_by: str
    subject_name: str
    consent_statement: str
    source: str
    created_at: str


class VoicePreviewRequest(_Strict):
    text: str | None = Field(default=None, min_length=1, max_length=500, pattern=r"\S")


class JoinTokenRequest(_Strict):
    participant_name: str | None = Field(default=None, min_length=1, max_length=80)


class JoinToken(_Strict):
    livekit_url: str
    room: str
    token: str
    participant_identity: str


class IntervalStats(_Strict):
    count: int
    p50: float | None
    p95: float | None
    max: float | None


class LatencyReport(_Strict):
    """Milliseconds; nearest-rank percentiles over recorded turns (see runtime/latency.py)."""

    turns: int
    t0_t4: IntervalStats
    t0_t1: IntervalStats
    t1_t2: IntervalStats
    t2_t3: IntervalStats
    t3_t4: IntervalStats
    t4_t5: IntervalStats


class LoginRequest(_Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserInfo(_Strict):
    user_id: str
    username: str
    role: Literal["admin", "user", "viewer"]
    must_change_password: bool


class LoginResponse(_Strict):
    user: UserInfo
    csrf_token: str


class ChangePasswordRequest(_Strict):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class Me(_Strict):
    kind: Literal["user", "token", "envkey"]
    role: str
    effective_role: Literal["admin", "operator", "viewer"]
    user_id: str | None
    username: str | None
    label: str | None
    must_change_password: bool
    csrf_token: str | None


class Guardrails(_Strict):
    header: str
    rules: list[str]


class ArchivedIdentity(_Strict):
    identity_id: str
    archived_at: str


# ---- Phase 2 / P2: admin management -----------------------------------------------------------------------------------

class AdminUser(_Strict):
    user_id: str
    username: str
    role: Literal["admin", "user", "viewer"]
    must_change_password: bool
    disabled: bool
    created_at: str
    updated_at: str
    last_login_at: str | None


class AdminUserCreate(_Strict):
    username: str = Field(min_length=1, max_length=64)
    temporary_password: str = Field(min_length=1, max_length=256)
    role: Literal["user", "viewer"] = "user"


class AdminUserPatch(_Strict):
    disabled: bool | None = None
    role: Literal["user", "viewer"] | None = None
    temporary_password: str | None = Field(default=None, min_length=1, max_length=256)


class ServiceTokenCreate(_Strict):
    label: str = Field(min_length=1, max_length=80, pattern=r"\S")
    role: Literal["viewer", "operator"]


class ServiceTokenInfo(_Strict):
    token_id: str
    label: str
    role: Literal["viewer", "operator"]
    created_at: str
    last_used_at: str | None
    revoked_at: str | None


class ServiceTokenCreated(ServiceTokenInfo):
    token: str


class AdminIdentity(IdentitySummary):
    archived_at: str | None


class IdentityOwnerPatch(_Strict):
    owner_user_id: str | None
    clear_voice: bool = False  # detach the persona's voice when the new owner cannot use it


class TemplatePatch(_Strict):
    disabled: bool | None = None
    shared: bool | None = None
    owner_user_id: str | None = None  # present-and-null = unowned; absent = unchanged (see model_fields_set)


class VoicePatch(_Strict):
    disabled: bool | None = None
    owner_user_id: str | None = None


class VoiceConsent(_Strict):
    authorized_by: str
    subject_name: str
    consent_statement: str
    source: str
    created_at: str


class AdminVoice(VoiceProfile):
    owner_user_id: str | None
    disabled: bool
    consent: VoiceConsent | None


class ReingestResult(_Strict):
    repo: str
    branch: str
    sha: str
    dirty: bool
    ingested: dict[str, int]
    skipped: list[dict[str, str]]
    missing_required: list[str]
