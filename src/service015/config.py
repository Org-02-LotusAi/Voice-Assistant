"""Environment-driven settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal, get_args

from pydantic import Field, field_validator
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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True)

    api_keys: Annotated[dict[str, Role], NoDecode] = Field(default_factory=dict, validation_alias="SERVICE015_API_KEYS")
    db_path: str = Field("./data/service015.db", validation_alias="SERVICE015_DB_PATH")
    host: str = Field("127.0.0.1", validation_alias="SERVICE015_HOST")
    port: int = Field(8015, validation_alias="SERVICE015_PORT")
    public_url: str = Field("http://localhost:8015", validation_alias="SERVICE015_PUBLIC_URL")

    llm_base_url: str = Field("https://api.groq.com/openai/v1", validation_alias="LLM_BASE_URL")
    llm_api_key: str = Field("", validation_alias="LLM_API_KEY")
    llm_model: str = Field("llama-3.3-70b-versatile", validation_alias="LLM_MODEL")

    livekit_url: str = Field("ws://localhost:7880", validation_alias="LIVEKIT_URL")
    livekit_api_key: str = Field("devkey", validation_alias="LIVEKIT_API_KEY")
    livekit_api_secret: str = Field("secret", validation_alias="LIVEKIT_API_SECRET")

    voicebox_url: str = Field("http://127.0.0.1:17493", validation_alias="VOICEBOX_URL")

    @field_validator("api_keys", mode="before")
    @classmethod
    def _parse_api_keys(cls, value: object) -> object:
        if isinstance(value, str):
            return parse_api_keys(value)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
