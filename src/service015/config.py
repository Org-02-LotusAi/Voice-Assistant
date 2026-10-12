"""Environment-driven settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal, get_args
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from service015.clientip import Network, parse_networks
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Role = Literal["admin", "operator", "viewer"]
ROLES: tuple[str, ...] = get_args(Role)


def parse_api_keys(raw: str) -> dict[str, Role]:
    """Parse "key1:admin,key2:operator" into {key: role}; raise ValueError on bad input."""
    keys: dict[str, Role] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        key, sep, role = item.rpartition(":")
        key, role = key.strip(), role.strip()
        if not sep or not key:
            raise ValueError("SERVICE015_API_KEYS entries must look like 'key:role'; found an entry missing the key or the role")
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r} in SERVICE015_API_KEYS; allowed roles: {', '.join(ROLES)}")
        if key in keys:
            raise ValueError("duplicate key in SERVICE015_API_KEYS")
        keys[key] = role  # type: ignore[assignment]
    return keys


MIN_KEY_LENGTH = 24
MIN_LIVEKIT_SECRET_LENGTH = 32
_DEV_LIVEKIT_KEYS = {"devkey"}
_DEV_LIVEKIT_SECRETS = {"secret", ""}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def is_local_public_url(public_url: str) -> bool:
    return (urlsplit(public_url).hostname or "") in _LOCAL_HOSTS


def check_api_keys_for_deployment(settings: "Settings") -> None:
    """Refuse placeholder or short API keys and LiveKit dev credentials when the service is not local-only.

    Local means SERVICE015_PUBLIC_URL points at localhost / 127.0.0.1. The message names the problem and the role,
    never the key itself. Raises ValueError.
    """
    host = urlsplit(settings.public_url).hostname or ""
    if host in _LOCAL_HOSTS:
        return
    problems: list[str] = []
    for key, role in settings.api_keys.items():
        if key.startswith("change-me"):
            problems.append(f"the {role} key is still a 'change-me' placeholder")
        elif len(key) < MIN_KEY_LENGTH:
            problems.append(f"the {role} key is shorter than {MIN_KEY_LENGTH} characters")
    if settings.livekit_api_key in _DEV_LIVEKIT_KEYS:
        problems.append("LIVEKIT_API_KEY is the dev default 'devkey'")
    if settings.livekit_api_secret in _DEV_LIVEKIT_SECRETS or len(settings.livekit_api_secret) < MIN_LIVEKIT_SECRET_LENGTH:
        problems.append(f"LIVEKIT_API_SECRET is the dev default or shorter than {MIN_LIVEKIT_SECRET_LENGTH} characters")
    if problems:
        raise ValueError(
            "refusing to start: SERVICE015_PUBLIC_URL is not localhost but the credentials are weak ("
            + "; ".join(problems)
            + "). Generate strong keys: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True, hide_input_in_errors=True
    )

    api_keys: Annotated[dict[str, Role], NoDecode] = Field(default_factory=dict, validation_alias="SERVICE015_API_KEYS", repr=False)
    # Bootstrap password for the first `admin` account (created only when no admin exists; forced change on first login).
    admin_password: str = Field("", validation_alias="SERVICE015_ADMIN_PASSWORD", repr=False)
    # Peers (IPs / CIDRs, comma-separated) whose X-Forwarded-For is believed when deriving the client IP for login throttling.
    repo_root: str = Field("", validation_alias="SERVICE015_REPO_ROOT")  # Service Expert sources / provenance; default: this checkout
    trusted_proxies: Annotated[tuple[Network, ...], NoDecode] = Field((), validation_alias="SERVICE015_TRUSTED_PROXIES")
    db_path: str = Field("./data/service015.db", validation_alias="SERVICE015_DB_PATH")
    host: str = Field("127.0.0.1", validation_alias="SERVICE015_HOST")
    port: int = Field(8015, validation_alias="SERVICE015_PORT")
    public_url: str = Field("http://localhost:8015", validation_alias="SERVICE015_PUBLIC_URL")

    llm_base_url: str = Field("https://api.groq.com/openai/v1", validation_alias="LLM_BASE_URL")
    llm_api_key: str = Field("", validation_alias="LLM_API_KEY")
    llm_model: str = Field("qwen/qwen3.8-27b", validation_alias="LLM_MODEL")
    # Sent as `reasoning_effort` on chat requests; empty = do not send the field (models that reject it).
    llm_reasoning_effort: str = Field("none", validation_alias="LLM_REASONING_EFFORT")

    livekit_url: str = Field("ws://localhost:7880", validation_alias="LIVEKIT_URL")
    # URL handed to browsers/humans (join tokens, UI CSP) when it differs from the server-side LIVEKIT_URL
    # (Docker: ws://livekit:7880 inside the network, wss://lk.example.com for browsers). Empty = use LIVEKIT_URL.
    livekit_public_url: str = Field("", validation_alias="LIVEKIT_PUBLIC_URL")
    livekit_api_key: str = Field("devkey", validation_alias="LIVEKIT_API_KEY")
    livekit_api_secret: str = Field("secret", validation_alias="LIVEKIT_API_SECRET")

    # Upper bound on Identities that are STARTING/READY/BUSY at once (each is a process with models resident).
    max_active_identities: int = Field(2, ge=1, validation_alias="SERVICE015_MAX_ACTIVE_IDENTITIES")
    livekit_agent_name: str = Field("service015-identity", validation_alias="LIVEKIT_AGENT_NAME")

    service014_mode: Literal["disabled", "http", "livekit_sip"] = Field("disabled", validation_alias="SERVICE014_MODE")
    service014_url: str = Field("", validation_alias="SERVICE014_URL")
    service014_api_key: str = Field("", validation_alias="SERVICE014_API_KEY", repr=False)
    service014_sip_trunk_id: str = Field("", validation_alias="SERVICE014_SIP_TRUNK_ID")
    # Comma-separated E.164 prefixes a dial may target. Empty = deny all on a public deployment, allow all on localhost.
    service014_allowed_destinations: Annotated[tuple[str, ...], NoDecode] = Field(
        (), validation_alias="SERVICE014_ALLOWED_DESTINATIONS"
    )

    voicebox_url: str = Field("http://127.0.0.1:17493", validation_alias="VOICEBOX_URL")
    voicebox_timeout_s: float = Field(30.0, gt=0, validation_alias="VOICEBOX_TIMEOUT_S")
    voice_engine_url: str = Field("http://127.0.0.1:17494", validation_alias="VOICE_ENGINE_URL")
    # Preview synthesis on CPU runs at RTF ~6-9 (15-40 s for a short sentence); GPU production is much faster.
    voice_engine_timeout_s: float = Field(120.0, gt=0, validation_alias="VOICE_ENGINE_TIMEOUT_S")

    # LLM rate-limit mitigation (Groq on-demand tier ~7,000 input tokens/min): no speculative generations, longer endpointing
    # delay, bounded chat history, bounded session recreation after unrecoverable LLM errors.
    preemptive_generation: bool = Field(False, validation_alias="SERVICE015_PREEMPTIVE_GENERATION")
    endpointing_min_delay_s: float = Field(0.6, ge=0.1, validation_alias="SERVICE015_ENDPOINTING_MIN_DELAY_S")
    max_history_turns: int = Field(8, ge=1, validation_alias="SERVICE015_MAX_HISTORY_TURNS")
    session_recovery_attempts: int = Field(5, ge=0, validation_alias="SERVICE015_SESSION_RECOVERY_ATTEMPTS")
    session_recovery_backoff_s: float = Field(15.0, gt=0, validation_alias="SERVICE015_SESSION_RECOVERY_BACKOFF_S")

    # Runtime log rotation and conversation-text retention.
    runtime_log_max_bytes: int = Field(5_000_000, ge=1000, validation_alias="SERVICE015_RUNTIME_LOG_MAX_BYTES")
    runtime_log_backups: int = Field(5, ge=0, validation_alias="SERVICE015_RUNTIME_LOG_BACKUPS")
    transcript_retention_days: int = Field(30, ge=0, validation_alias="SERVICE015_TRANSCRIPT_RETENTION_DAYS")

    moonshine_arch: Literal["tiny", "small", "medium"] = Field("small", validation_alias="MOONSHINE_ARCH")
    moonshine_language: str = Field("en", validation_alias="MOONSHINE_LANGUAGE")

    @property
    def livekit_client_url(self) -> str:
        """The LiveKit URL a human's browser or SIP-less client should connect to."""
        return self.livekit_public_url or self.livekit_url

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def _parse_trusted_proxies(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return parse_networks(value)
            except ValueError as exc:
                raise ValueError("SERVICE015_TRUSTED_PROXIES entries must be IP addresses or CIDR networks") from exc
        return value

    @field_validator("service014_allowed_destinations", mode="before")
    @classmethod
    def _parse_destinations(cls, value: object) -> object:
        if isinstance(value, str):
            prefixes = tuple(p.strip() for p in value.split(",") if p.strip())
            for p in prefixes:
                if not p.startswith("+") or not p[1:].isdigit():
                    raise ValueError("SERVICE014_ALLOWED_DESTINATIONS entries must be E.164 prefixes such as +91")
            return prefixes
        return value

    @field_validator("api_keys", mode="before")
    @classmethod
    def _parse_api_keys(cls, value: object) -> object:
        if isinstance(value, str):
            return parse_api_keys(value)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
