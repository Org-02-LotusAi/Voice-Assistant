"""Deterministic Template + overrides -> ResolvedIdentity resolution (pure, no I/O)."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from service015.errors import ValidationFailed
from service015.models import TemplateBody

MAX_TEXT_LEN = 4000
_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")
_PLACEHOLDER_RE = re.compile(r"\{(name|company|role)\}")
# Self-facts are rendered into the instructions line by line; they must stay single-line.
MAX_NAME_LEN = 80
MAX_FACT_LEN = 120  # company / role
_NAME_EXTRA_CHARS = frozenset(" '-.")
_BOOL_KEYS = ("conversation.barge_in",)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SelfBlock(_Frozen):
    identity_id: str
    name: str
    archetype: str
    template_version: int
    company: str | None
    role: str | None
    language: str


class PersonaBlock(_Frozen):
    objective: str
    style: str
    instructions: str


class ConversationBlock(_Frozen):
    greeting: str
    barge_in: bool
    speaking_style: str


class VoiceBlock(_Frozen):
    profile_id: str | None
    engine: str


class ParticipantBlock(_Frozen):
    participant_identity: str
    default_room: str


class ResolvedIdentity(_Frozen):
    """Instance snapshot. The JSON key is `self`; the Python attribute is `self_`."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    identity_id: str
    template_id: str
    template_version: int
    self_: SelfBlock = Field(alias="self")
    persona: PersonaBlock
    conversation: ConversationBlock
    voice: VoiceBlock
    participant: ParticipantBlock
    overrides: dict[str, Any]
    resolved_hash: str

    def to_json_obj(self) -> dict[str, Any]:
        """JSON-ready dict using the contract key `self`."""
        return self.model_dump(by_alias=True, mode="json")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate_overrides(template: TemplateBody, overrides: dict[str, Any]) -> None:
    """Raise ValidationFailed unless `overrides` is allowed by, and complete for, `template`."""
    allowed = list(template.allowed_overrides)
    for key in overrides:
        if key not in template.allowed_overrides:
            raise ValidationFailed(
                "override_not_allowed",
                f"override {key!r} is not allowed by template {template.template_id!r} v{template.version}; "
                f"allowed overrides: {allowed}",
                details={"field": key, "allowed": allowed},
            )
    for key, value in overrides.items():
        _check_type(key, value)
    for key in template.required_instance_fields:
        if key not in overrides:
            raise ValidationFailed(
                "missing_required_field",
                f"required field {key!r} is missing for template {template.template_id!r} v{template.version}",
                details={"field": key, "required": list(template.required_instance_fields)},
            )


def _check_type(key: str, value: Any) -> None:
    if key in _BOOL_KEYS:
        if not isinstance(value, bool):
            raise ValidationFailed("invalid_override", f"override {key!r} must be a boolean", details={"field": key})
        return
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed("invalid_override", f"override {key!r} must be a non-empty string", details={"field": key})
    if len(value) > MAX_TEXT_LEN:
        raise ValidationFailed(
            "invalid_override", f"override {key!r} exceeds {MAX_TEXT_LEN} characters", details={"field": key}
        )
    if key == "language" and not _LANGUAGE_RE.match(value):
        raise ValidationFailed(
            "invalid_override", f"override 'language' {value!r} must look like 'en' or 'en-US'", details={"field": key}
        )
    if key == "name":
        _check_name(value)
    elif key in ("company", "role"):
        _check_fact(key, value)
    elif key == "language" and ("\n" in value or "\r" in value):
        raise ValidationFailed("invalid_override", f"override {key!r} must be a single line", details={"field": key})


def _check_name(value: str) -> None:
    ok = (
        len(value) <= MAX_NAME_LEN
        and any(ch.isalpha() for ch in value)
        and all(ch.isalpha() or ch in _NAME_EXTRA_CHARS for ch in value)
    )
    if not ok:
        raise ValidationFailed(
            "invalid_override",
            f"override 'name' must be at most {MAX_NAME_LEN} characters: letters, spaces, apostrophe, hyphen, period only",
            details={"field": "name"},
        )


def _check_fact(key: str, value: str) -> None:
    if len(value) > MAX_FACT_LEN or any(unicodedata.category(ch)[0] in "CZ" and ch != " " for ch in value):
        raise ValidationFailed(
            "invalid_override",
            f"override {key!r} must be a single line of at most {MAX_FACT_LEN} characters with no control characters",
            details={"field": key},
        )


def compute_hash(body: dict[str, Any]) -> str:
    """sha256 of the canonical JSON of a resolved body (without `resolved_hash`)."""
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _require_set_facts(key: str, text: str, facts: dict[str, str | None]) -> None:
    """An overridden text may only use placeholders whose facts are set (no broken spoken sentences)."""
    unset = sorted({m.group(1) for m in _PLACEHOLDER_RE.finditer(text) if facts[m.group(1)] is None})
    if unset:
        raise ValidationFailed(
            "invalid_override",
            f"override {key!r} uses placeholders {unset} whose facts are not set",
            details={"field": key, "unset_placeholders": unset},
        )


def _substitute(text: str, facts: dict[str, str | None]) -> str:
    """Replace only {name}/{company}/{role}, in a single pass, so other braces in user text are inert."""
    blanked = False

    def repl(m: re.Match[str]) -> str:
        nonlocal blanked
        val = facts[m.group(1)]
        if val is None:
            blanked = True
            return ""
        return val

    out = _PLACEHOLDER_RE.sub(repl, text)
    if blanked:  # tidy the gap left by an unset fact; never emit the literal "None"
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r" +([.,!?;:])", r"\1", out)
    return out


def resolve(template: TemplateBody, overrides: dict[str, Any], identity_id: str) -> ResolvedIdentity:
    """Template defaults -> overrides -> placeholder substitution. Same input => same resolved_hash."""
    validate_overrides(template, overrides)

    d = template.default_self_facts
    name = overrides["name"] if "name" in overrides else template.name
    company = overrides.get("company", d.company)
    role = overrides.get("role", d.role)
    language = overrides.get("language", d.language)
    facts: dict[str, str | None] = {"name": name, "company": company, "role": role}

    for key in ("greeting", "persona.instructions"):
        if key in overrides:
            _require_set_facts(key, overrides[key], facts)
    persona_instructions = overrides.get("persona.instructions", template.persona.instructions)
    greeting = overrides.get("greeting", template.conversation.greeting)
    body: dict[str, Any] = {
        "identity_id": identity_id,
        "template_id": template.template_id,
        "template_version": template.version,
        "self": {
            "identity_id": identity_id,
            "name": name,
            "archetype": template.template_id,
            "template_version": template.version,
            "company": company,
            "role": role,
            "language": language,
        },
        "persona": {
            "objective": overrides.get("persona.objective", template.persona.objective),
            "style": overrides.get("persona.style", template.persona.style),
            "instructions": _substitute(persona_instructions, facts),
        },
        "conversation": {
            "greeting": _substitute(greeting, facts),
            "barge_in": overrides.get("conversation.barge_in", template.conversation.barge_in),
            "speaking_style": overrides.get("conversation.speaking_style", template.conversation.speaking_style),
        },
        "voice": {
            "profile_id": overrides.get("voice.profile_id", template.voice.default_voice_profile_id),
            "engine": template.voice.engine,
        },
        "participant": {"participant_identity": identity_id, "default_room": identity_id},
        "overrides": dict(overrides),
    }
    return ResolvedIdentity.model_validate({**body, "resolved_hash": compute_hash(body)})
