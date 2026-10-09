"""API-key RBAC: `require_role(role)` FastAPI dependency factory."""

from __future__ import annotations

import hmac
from typing import Callable

from fastapi import Request

from service015.config import Role, Settings
from service015.errors import DomainError

RANK: dict[str, int] = {"viewer": 1, "operator": 2, "admin": 3}


class Unauthorized(DomainError):
    default_status = 401

    def __init__(self, message: str) -> None:
        super().__init__("unauthorized", message)
        self.headers = {"WWW-Authenticate": "Bearer"}


class Forbidden(DomainError):
    default_status = 403


def authenticate(settings: Settings, authorization: str | None) -> Role:
    """Return the role of the bearer key, or raise Unauthorized."""
    if not authorization:
        raise Unauthorized("missing Authorization header; expected 'Bearer <key>'")
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("Authorization header must be 'Bearer <key>'")
    found: Role | None = None
    for key, role in settings.api_keys.items():  # constant-time compare, no early exit
        if hmac.compare_digest(key.encode(), token.encode()):
            found = role
    if found is None:
        raise Unauthorized("unknown API key")
    return found


def require_role(role: str) -> Callable[[Request], Role]:
    """Dependency allowing keys whose role is `role` or higher (admin > operator > viewer)."""
    if role not in RANK:
        raise ValueError(f"unknown role {role!r}")

    def dependency(request: Request) -> Role:
        actual = authenticate(request.app.state.settings, request.headers.get("authorization"))
        if RANK[actual] < RANK[role]:
            raise Forbidden(
                "forbidden",
                f"this operation requires role {role!r}; your key has role {actual!r}",
                details={"required_role": role, "role": actual},
            )
        return actual

    dependency.required_role = role  # type: ignore[attr-defined]
    return dependency
