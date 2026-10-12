"""Accounts: users, sessions, DB service tokens, login throttling and the bootstrap admin.

Security notes: passwords are argon2id hashes; session cookies and service tokens are random and only their sha256 is stored;
login failures are throttled per username and per IP; nothing here logs a password, token or session id.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from service015.config import is_local_public_url
from service015.errors import Conflict, DomainError, NotFound, ValidationFailed

log = logging.getLogger("service015.accounts")

USER_ROLES = ("admin", "user", "viewer")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
PASSWORD_MIN = 12
PASSWORD_MAX = 128
COMMON_PASSWORDS = frozenset(
    p.lower()
    for p in (
        "password1234", "password12345", "passwordpassword", "123456789012", "1234567890123", "123456123456",
        "qwertyuiop12", "qwertyuiopas", "qwerty123456", "administrator", "administrator1", "adminadminadmin",
        "admin1234567", "changeme1234", "changemechangeme", "letmein12345", "welcome12345", "iloveyou1234",
        "abcdefghijkl", "abc123abc123", "000000000000", "111111111111", "aaaaaaaaaaaa", "p@ssw0rd1234",
        "passw0rd1234", "change-me-admin-password", "trustno1trustno1", "service015", "service015admin", "voicesow12345",
    )
)

IDLE_TIMEOUT_S = 12 * 3600
ABSOLUTE_TIMEOUT_S = 7 * 24 * 3600
SESSION_TOUCH_S = 60.0

THROTTLE_WINDOW_S = 15 * 60
THROTTLE_USER_FAILURES = 5
THROTTLE_IP_FAILURES = 20  # higher than per-user: an office behind one NAT must not lock itself out

_hasher = PasswordHasher()  # argon2id with the library's current defaults
_dummy_hash: str | None = None


class TooManyAttempts(DomainError):
    default_status = 429

    def __init__(self, retry_after_s: int) -> None:
        super().__init__(
            "too_many_attempts", "too many failed sign-in attempts; try again later", details={"retry_after_s": retry_after_s}
        )
        self.headers = {"Retry-After": str(retry_after_s)}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# -- passwords ---------------------------------------------------------------------------------------------------------

def validate_username(username: str) -> str:
    if not isinstance(username, str) or not USERNAME_RE.fullmatch(username):
        raise ValidationFailed(
            "invalid_username",
            "username must be 3-32 characters: lowercase letters, digits, '_', '.', '-', starting with a letter or digit",
            details={"field": "username"},
        )
    return username


def validate_password(password: str, username: str = "") -> None:
    """Policy: 12-128 characters, not the username, not a common password. Raises ValidationFailed `weak_password`."""
    problem = None
    if len(password) < PASSWORD_MIN:
        problem = f"must be at least {PASSWORD_MIN} characters"
    elif len(password) > PASSWORD_MAX:
        problem = f"must be at most {PASSWORD_MAX} characters"
    elif username and password.lower() == username.lower():
        problem = "must not equal the username"
    elif password.lower() in COMMON_PASSWORDS:
        problem = "is too common"
    if problem:
        raise ValidationFailed("weak_password", f"password {problem}", details={"field": "password"})


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-work check: an unknown user is verified against a dummy hash so timing does not reveal existence."""
    global _dummy_hash
    if password_hash is None:
        if _dummy_hash is None:
            _dummy_hash = _hasher.hash(secrets.token_urlsafe(16))
        try:
            _hasher.verify(_dummy_hash, password)
        except VerificationError:
            pass
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# -- users -------------------------------------------------------------------------------------------------------------

def create_user(
    conn: sqlite3.Connection, username: str, password: str, role: str, must_change_password: bool = True
) -> sqlite3.Row:
    validate_username(username)
    if role not in USER_ROLES:
        raise ValidationFailed("invalid_role", f"role must be one of {list(USER_ROLES)}", details={"field": "role"})
    validate_password(password, username)
    user_id = "usr_" + secrets.token_hex(8)
    now = _now_iso()
    try:
        conn.execute(
            "INSERT INTO users (user_id, username, role, password_hash, must_change_password, disabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (user_id, username, role, hash_password(password), int(must_change_password), now, now),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise Conflict("username_taken", f"username {username!r} is already taken", details={"field": "username"}) from exc
    return get_user(conn, user_id)


def get_user(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        raise NotFound("user_not_found", f"user {user_id!r} not found", details={"user_id": user_id})
    return row


def find_user(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def set_password(
    conn: sqlite3.Connection, user_id: str, new_password: str, must_change: bool, keep_session: str | None = None
) -> None:
    """Replace the hash and revoke every session of the user except `keep_session` (a session_hash)."""
    user = get_user(conn, user_id)
    validate_password(new_password, user["username"])
    conn.execute(
        "UPDATE users SET password_hash = ?, must_change_password = ?, updated_at = ? WHERE user_id = ?",
        (hash_password(new_password), int(must_change), _now_iso(), user_id),
    )
    conn.execute("DELETE FROM sessions WHERE user_id = ? AND session_hash IS NOT ?", (user_id, keep_session))
    conn.commit()


# -- login throttling --------------------------------------------------------------------------------------------------

def check_throttle(conn: sqlite3.Connection, username: str, ip: str, now: float | None = None) -> None:
    """Raise TooManyAttempts (429) once the username or the IP has too many failures inside the window."""
    now = time.time() if now is None else now
    since = now - THROTTLE_WINDOW_S
    for column, value, limit in (("username", username, THROTTLE_USER_FAILURES), ("ip", ip, THROTTLE_IP_FAILURES)):
        rows = conn.execute(
            f"SELECT at_epoch FROM login_attempts WHERE {column} = ? AND success = 0 AND at_epoch > ? ORDER BY at_epoch",
            (value, since),
        ).fetchall()
        if len(rows) >= limit:
            oldest_relevant = rows[len(rows) - limit]["at_epoch"]
            raise TooManyAttempts(max(1, int(oldest_relevant + THROTTLE_WINDOW_S - now) + 1))


def record_attempt(conn: sqlite3.Connection, username: str, ip: str, success: bool, now: float | None = None) -> None:
    now = time.time() if now is None else now
    conn.execute("DELETE FROM login_attempts WHERE at_epoch < ?", (now - 24 * 3600,))
    conn.execute(
        "INSERT INTO login_attempts (username, ip, success, at_epoch) VALUES (?, ?, ?, ?)", (username, ip, int(success), now)
    )
    if success:  # a good sign-in clears that username's failure streak
        conn.execute("DELETE FROM login_attempts WHERE username = ? AND success = 0", (username,))
    conn.commit()


# -- sessions ----------------------------------------------------------------------------------------------------------

def create_session(conn: sqlite3.Connection, user_id: str, ip: str | None, now: float | None = None) -> tuple[str, str]:
    """New session -> (opaque cookie value, csrf token). Only the cookie value's sha256 is stored."""
    now = time.time() if now is None else now
    raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    conn.execute("DELETE FROM sessions WHERE expires < ? OR last_seen < ?", (now, now - IDLE_TIMEOUT_S))
    conn.execute(
        "INSERT INTO sessions (session_hash, user_id, csrf_token, created_at, last_seen, expires, ip) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sha256_hex(raw), user_id, csrf, _now_iso(), now, now + ABSOLUTE_TIMEOUT_S, ip),
    )
    conn.execute("UPDATE users SET last_login_at = ? WHERE user_id = ?", (_now_iso(), user_id))
    conn.commit()
    return raw, csrf


def load_session(conn: sqlite3.Connection, raw: str, now: float | None = None) -> sqlite3.Row | None:
    """The live session joined with its user, or None (unknown, expired, idle, or the user is disabled). Slides last_seen."""
    if not raw or len(raw) > 200:
        return None
    now = time.time() if now is None else now
    h = sha256_hex(raw)
    row = conn.execute(
        "SELECT s.session_hash, s.user_id, s.csrf_token, s.last_seen, s.expires, u.username, u.role, "
        "u.must_change_password, u.disabled FROM sessions s JOIN users u ON u.user_id = s.user_id WHERE s.session_hash = ?",
        (h,),
    ).fetchone()
    if row is None:
        return None
    if row["expires"] < now or now - row["last_seen"] > IDLE_TIMEOUT_S or row["disabled"]:
        conn.execute("DELETE FROM sessions WHERE session_hash = ?", (h,))
        conn.commit()
        return None
    if now - row["last_seen"] > SESSION_TOUCH_S:
        conn.execute("UPDATE sessions SET last_seen = ? WHERE session_hash = ?", (now, h))
        conn.commit()
    return row


def revoke_session(conn: sqlite3.Connection, raw: str) -> None:
    conn.execute("DELETE FROM sessions WHERE session_hash = ?", (sha256_hex(raw),))
    conn.commit()


# -- service tokens ----------------------------------------------------------------------------------------------------

def create_service_token(
    conn: sqlite3.Connection, label: str, role: str, created_by: str | None = None
) -> tuple[sqlite3.Row, str]:
    """-> (row, plaintext token). The plaintext is returned once and never stored."""
    if role not in ("viewer", "operator"):
        raise ValidationFailed("invalid_role", "service token role must be 'viewer' or 'operator'", details={"field": "role"})
    token = "s015_" + secrets.token_urlsafe(32)
    token_id = "tok_" + secrets.token_hex(6)
    conn.execute(
        "INSERT INTO service_tokens (token_id, label, role, token_hash, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (token_id, label, role, sha256_hex(token), created_by, _now_iso()),
    )
    conn.commit()
    return conn.execute("SELECT * FROM service_tokens WHERE token_id = ?", (token_id,)).fetchone(), token


def lookup_service_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM service_tokens WHERE token_hash = ? AND revoked_at IS NULL", (sha256_hex(token),)).fetchone()
    if row is not None:
        last = row["last_used_at"]
        if last is None or (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() > SESSION_TOUCH_S:
            conn.execute("UPDATE service_tokens SET last_used_at = ? WHERE token_id = ?", (_now_iso(), row["token_id"]))
            conn.commit()
    return row


def revoke_service_token(conn: sqlite3.Connection, token_id: str) -> None:
    cur = conn.execute(
        "UPDATE service_tokens SET revoked_at = ? WHERE token_id = ? AND revoked_at IS NULL", (_now_iso(), token_id)
    )
    conn.commit()
    if cur.rowcount == 0:
        raise NotFound(
            "token_not_found", f"service token {token_id!r} not found or already revoked", details={"token_id": token_id}
        )


# -- bootstrap ---------------------------------------------------------------------------------------------------------

def check_bootstrap_password(password: str) -> None:
    """Static startup check: a configured bootstrap password must satisfy the policy (any deployment)."""
    if password:
        try:
            validate_password(password, "admin")
        except ValidationFailed as exc:
            raise ValueError(f"refusing to start: SERVICE015_ADMIN_PASSWORD is invalid ({exc.message})") from exc


def bootstrap_admin(conn: sqlite3.Connection, password: str, public_url: str) -> bool:
    """Create `admin` (must change password on first login) when no admin exists; adopt pre-existing rows. True if created.

    No password configured: refuse (ValueError) on a non-local deployment, warn and carry on locally (API keys still work).
    """
    if conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
        return False
    if not password:
        if not is_local_public_url(public_url):
            raise ValueError(
                "refusing to start: no admin account exists and SERVICE015_ADMIN_PASSWORD is not set "
                "(required when SERVICE015_PUBLIC_URL is not localhost)"
            )
        log.warning(
            "no admin account exists and SERVICE015_ADMIN_PASSWORD is not set: human sign-in is unavailable (API keys still work)"
        )
        return False
    check_bootstrap_password(password)
    try:
        admin = create_user(conn, "admin", password, "admin", must_change_password=True)
    except Conflict as exc:
        if conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
            return False  # another starter won the race
        raise ValueError("refusing to start: username 'admin' exists but is not an admin; cannot bootstrap") from exc
    for table in ("identities", "templates", "voice_authorizations"):
        conn.execute(f"UPDATE {table} SET owner_user_id = ? WHERE owner_user_id IS NULL", (admin["user_id"],))
    conn.commit()
    log.info("bootstrap admin account created; password change required on first login")
    return True


def user_info(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "role": row["role"],
        "must_change_password": bool(row["must_change_password"]),
    }
