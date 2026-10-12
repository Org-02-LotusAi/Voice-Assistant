"""Pydantic models for the Template contract and API error body."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

OVERRIDE_UNIVERSE: tuple[str, ...] = (
    "name",
    "company",
    "role",
    "language",
    "persona.instructions",
    "persona.objective",
    "persona.style",
    "greeting",
    "voice.profile_id",
    "conversation.speaking_style",
    "conversation.barge_in",
)

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
FACT_PLACEHOLDERS: tuple[str, ...] = ("name", "company", "role")

TEMPLATE_ID_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
STORED_TEMPLATE_ID_PATTERN = r"^([a-z0-9][a-z0-9_.-]{2,31}\.)?[a-z0-9]+(-[a-z0-9]+)*$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SelfFacts(_Strict):
    role: str | None = None
    company: str | None = None
    language: str = "en-US"


class Persona(_Strict):
    objective: str = Field(min_length=1)
    style: str = Field(min_length=1)
    instructions: str = Field(min_length=1)


class Conversation(_Strict):
    greeting: str = Field(min_length=1)
    barge_in: bool = True
    speaking_style: str = Field(min_length=1)


class VoiceDefaults(_Strict):
    default_voice_profile_id: str | None = None
    engine: str = "chatterbox-turbo"


class _TemplateFields(_Strict):
    # Stored ids: a shared slug, or `<owner_username>.<slug>` for a private family (the dot is reserved for that namespace).
    template_id: str = Field(pattern=STORED_TEMPLATE_ID_PATTERN, max_length=100)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    default_self_facts: SelfFacts = Field(default_factory=SelfFacts)
    persona: Persona
    conversation: Conversation
    voice: VoiceDefaults = Field(default_factory=VoiceDefaults)
    required_instance_fields: list[str] = Field(default_factory=list)
    allowed_overrides: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_override_keys(self) -> "_TemplateFields":
        unknown = sorted(set(self.allowed_overrides) - set(OVERRIDE_UNIVERSE))
        if unknown:
            raise ValueError(f"allowed_overrides contains unknown keys {unknown}; allowed keys: {list(OVERRIDE_UNIVERSE)}")
        if len(set(self.allowed_overrides)) != len(self.allowed_overrides):
            raise ValueError("allowed_overrides contains duplicates")
        missing = sorted(set(self.required_instance_fields) - set(self.allowed_overrides))
        if missing:
            raise ValueError(f"required_instance_fields {missing} must also be listed in allowed_overrides")
        self._check_placeholders()
        return self

    def _check_placeholders(self) -> None:
        """Every {name}/{company}/{role} spoken in text must be guaranteed to have a value."""
        used: set[str] = set()
        for text in (self.conversation.greeting, self.persona.instructions):
            used.update(m for m in _PLACEHOLDER_RE.findall(text) if m in FACT_PLACEHOLDERS)
        unguaranteed = sorted(
            f for f in used if f not in self.required_instance_fields and getattr(self.default_self_facts, f, None) is None
        )
        if unguaranteed:
            raise ValueError(
                f"placeholders {unguaranteed} are used in greeting/persona.instructions but are neither in "
                "required_instance_fields nor given a non-null default in default_self_facts"
            )


class TemplateCreate(_TemplateFields):
    """Request body for creating a template (v1) or a new version of an existing one. The slug has no dot: a private
    template is stored by the server as `<owner_username>.<slug>`."""

    template_id: str = Field(pattern=TEMPLATE_ID_PATTERN, max_length=64)


class TemplateBody(_TemplateFields):
    """A stored, immutable template version."""

    version: int = Field(ge=1)
    created_at: str


class TemplateSummary(_Strict):
    template_id: str
    latest_version: int
    name: str
    description: str
    shared: bool = True
    disabled: bool = False
    owner_user_id: str | None = None


class ErrorDetail(_Strict):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorBody(_Strict):
    error: ErrorDetail
