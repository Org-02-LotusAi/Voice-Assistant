"""Human authentication: login / logout / me / change-password (session cookie + CSRF token)."""

from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, Depends, Request, Response
from urllib.parse import urlsplit

from service015 import accounts
from service015.api.deps import get_conn
from service015.api.schemas import ChangePasswordRequest, LoginRequest, LoginResponse, Me
from service015.auth import SESSION_COOKIE, Forbidden, Principal, require_principal
from service015.errors import DomainError, ValidationFailed

log = logging.getLogger("service015.api")
router = APIRouter(tags=["auth"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _secure_cookie(request: Request) -> bool:
    return urlsplit(request.app.state.settings.public_url).scheme == "https" or request.url.scheme == "https"


def _invalid_credentials() -> DomainError:
    return DomainError("invalid_credentials", "invalid username or password", status=401)


@router.post("/auth/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response, conn: sqlite3.Connection = Depends(get_conn)):
    username = body.username.strip().lower()
    ip = _client_ip(request)
    accounts.check_throttle(conn, username, ip)  # 429 before any password work
    user = accounts.find_user(conn, username)
    ok = accounts.verify_password(user["password_hash"] if user else None, body.password)
    if not ok or user is None or user["disabled"]:
        accounts.record_attempt(conn, username, ip, success=False)
        raise _invalid_credentials()
    accounts.record_attempt(conn, username, ip, success=True)
    raw, csrf = accounts.create_session(conn, user["user_id"], ip)
    response.set_cookie(SESSION_COOKIE, raw, httponly=True, samesite="strict", secure=_secure_cookie(request), path="/")
    log.info("login ok user=%s", user["user_id"])
    return LoginResponse(user=accounts.user_info(user), csrf_token=csrf)


@router.post(
    "/auth/logout",
    status_code=204,
    response_class=Response,
)
def logout(
    request: Request,
    response: Response,
    principal: Principal = Depends(require_principal("viewer", allow_password_change_pending=True)),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Ends the caller's session (a no-op for bearer callers) and clears the cookie."""
    if principal.session_hash is not None:
        conn.execute("DELETE FROM sessions WHERE session_hash = ?", (principal.session_hash,))
        conn.commit()
    out = Response(status_code=204)
    out.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict", secure=_secure_cookie(request))
    return out


@router.get("/auth/me", response_model=Me)
def me(principal: Principal = Depends(require_principal("viewer", allow_password_change_pending=True))):
    return Me(
        kind=principal.kind,
        role=principal.role,
        effective_role=principal.effective_role,
        user_id=principal.user_id,
        username=principal.username,
        label=principal.label,
        must_change_password=principal.must_change_password,
        csrf_token=principal.csrf_token,
    )


@router.post("/auth/change-password", response_model=Me)
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    principal: Principal = Depends(require_principal("viewer", allow_password_change_pending=True)),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Session callers only. Verifies the current password (failures count toward throttling), applies the password policy,
    clears `must_change_password` and revokes the account's other sessions."""
    if principal.kind != "user" or principal.user_id is None:
        raise Forbidden("session_required", "only a signed-in account can change its password")
    user = accounts.get_user(conn, principal.user_id)
    ip = _client_ip(request)
    accounts.check_throttle(conn, user["username"], ip)
    if not accounts.verify_password(user["password_hash"], body.current_password):
        accounts.record_attempt(conn, user["username"], ip, success=False)
        raise ValidationFailed("invalid_credentials", "the current password is incorrect", details={"field": "current_password"})
    if body.new_password == body.current_password:
        raise ValidationFailed(
            "password_unchanged", "the new password must differ from the current one", details={"field": "new_password"}
        )
    accounts.set_password(conn, principal.user_id, body.new_password, must_change=False, keep_session=principal.session_hash)
    log.info("password changed user=%s", principal.user_id)
    return Me(
        kind="user",
        role=principal.role,
        effective_role=principal.effective_role,
        user_id=principal.user_id,
        username=principal.username,
        label=None,
        must_change_password=False,
        csrf_token=principal.csrf_token,
    )
