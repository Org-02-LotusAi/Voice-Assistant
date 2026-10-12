"""Principals and RBAC.

Every request resolves to a `Principal`: a human session (cookie `s015_session`), an env API key, or a DB service token.
Roles: `admin` (humans and tokens), `user` (human, owner-scoped operator), `operator` / `viewer` (tokens and viewer humans).
The dependency factories `require_role(role)` / `require_principal(role)` accept a principal whose effective rank is at
least `role` (admin > operator > viewer; a `user` ranks as operator).
"""

from __future__ import annotations

import hmac
import sqlite3
from dataclasses import dataclass
from typing import Callable, Literal

from fastapi import Depends, Request

from service015 import accounts
from service015.api.deps import get_conn
from service015.config import Role, Settings
from service015.errors import DomainError
from service015.scope import UNRESTRICTED, Visibility

RANK: dict[str, int] = {"viewer": 1, "operator": 2, "admin": 3}
SESSION_COOKIE = "s015_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class Unauthorized(DomainError):
    default_status = 401

    def __init__(self, message: str) -> None:
        super().__init__("unauthorized", message)
        self.headers = {"WWW-Authenticate": "Bearer"}


class Forbidden(DomainError):
    default_status = 403


@dataclass(frozen=True)
class Principal:
    kind: Literal["user", "token", "envkey"]
    role: str  # user kind: admin|user|viewer; token/envkey: admin|operator|viewer
    user_id: str | None = None
    username: str | None = None
    token_id: str | None = None
    label: str | None = None
    session_hash: str | None = None
    csrf_token: str | None = None
    must_change_password: bool = False

    @property
    def effective_role(self) -> Role:
        return "operator" if self.role == "user" else self.role  # type: ignore[return-value]

    @property
    def owner_scope(self) -> str | None:
        """The user id whose resources this principal is limited to (identities); None = may act on all owners."""
        return self.user_id if self.kind == "user" and self.role == "user" else None

    @property
    def visibility(self) -> Visibility:
        """Template / voice visibility: human `user` and `viewer` see shared plus their own; admin, tokens and env keys all."""
        if self.kind == "user" and self.role in ("user", "viewer"):
            return Visibility(True, self.user_id)
        return UNRESTRICTED

    @property
    def owner_id_for_new(self) -> str | None:
        """Owner recorded on resources this principal creates (None for machine principals)."""
        return self.user_id

    def describe(self) -> str:
        return "account" if self.kind == "user" else "key"


def _bearer_principal(settings: Settings, conn: sqlite3.Connection | None, authorization: str) -> Principal:
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("Authorization header must be 'Bearer <key>'")
    found: Role | None = None
    for key, role in settings.api_keys.items():  # constant-time compare, no early exit
        if hmac.compare_digest(key.encode(), token.encode()):
            found = role
    if found is not None:
        return Principal(kind="envkey", role=found)
    if conn is not None:
        row = accounts.lookup_service_token(conn, token)
        if row is not None:
            return Principal(kind="token", role=row["role"], token_id=row["token_id"], label=row["label"])
    raise Unauthorized("unknown API key")


def resolve_principal(
    request: Request, conn: sqlite3.Connection | None, allow_password_change_pending: bool = False
) -> Principal:
    """Bearer (env key, then DB service token) wins; otherwise the session cookie. Raises Unauthorized / Forbidden."""
    settings: Settings = request.app.state.settings
    authorization = request.headers.get("authorization")
    if authorization:
        return _bearer_principal(settings, conn, authorization)
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        raise Unauthorized("missing Authorization header; expected 'Bearer <key>' (or a signed-in session)")
    if conn is None:
        raise Unauthorized("session lookup unavailable")
    row = accounts.load_session(conn, cookie)
    if row is None:
        raise Unauthorized("session is missing, expired or revoked; sign in again")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not hmac.compare_digest(sent.encode(), row["csrf_token"].encode()):
            raise Forbidden("csrf_failed", "a valid X-CSRF-Token header is required for session-authenticated writes")
    principal = Principal(
        kind="user",
        role=row["role"],
        user_id=row["user_id"],
        username=row["username"],
        session_hash=row["session_hash"],
        csrf_token=row["csrf_token"],
        must_change_password=bool(row["must_change_password"]),
    )
    if principal.must_change_password and not allow_password_change_pending:
        raise Forbidden(
            "password_change_required", "you must change your password before using this service",
            details={"change_password_path": "/auth/change-password"},
        )
    return principal


def _check_rank(principal: Principal, role: str) -> None:
    actual = principal.effective_role
    if RANK[actual] < RANK[role]:
        raise Forbidden(
            "forbidden",
            f"this operation requires role {role!r}; your {principal.describe()} has role {principal.role!r}",
            details={"required_role": role, "role": principal.role},
        )


def ensure_author(principal: Principal) -> None:
    """Creating templates / voices: human accounts (`user`, `admin`) and admin machine credentials. Operator keys and tokens
    keep their Phase-1 inability to author shared content."""
    if principal.kind != "user" and principal.effective_role != "admin":
        raise Forbidden(
            "forbidden",
            f"this operation requires role 'admin' or a signed-in user account; your {principal.describe()} has role {principal.role!r}",
            details={"required_role": "admin", "role": principal.role},
        )


def require_principal(role: str, allow_password_change_pending: bool = False) -> Callable[..., Principal]:
    """Dependency returning the authenticated Principal, 401/403 otherwise."""
    if role not in RANK:
        raise ValueError(f"unknown role {role!r}")

    def dependency(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Principal:
        principal = resolve_principal(request, conn, allow_password_change_pending)
        _check_rank(principal, role)
        return principal

    dependency.required_role = role  # type: ignore[attr-defined]
    return dependency


def require_role(role: str, allow_password_change_pending: bool = False) -> Callable[..., Role]:
    """Like `require_principal` but yields the effective Role string (admin|operator|viewer) for role-only callers."""
    inner = require_principal(role, allow_password_change_pending)

    def dependency(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Role:
        return inner(request, conn).effective_role

    dependency.required_role = role  # type: ignore[attr-defined]
    return dependency
