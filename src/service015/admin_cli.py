"""Operator console script: `service015-admin reset-password <username>`.

Out-of-band account recovery that needs shell access to the host (so it works when nobody can sign in). The new password is read
from `SERVICE015_NEW_PASSWORD` or prompted for twice without echo; it is never printed or logged. The account is flagged
`must_change_password`, re-enabled, and all its sessions are revoked.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from service015 import accounts
from service015.config import get_settings
from service015.db import connect
from service015.errors import DomainError

ENV_VAR = "SERVICE015_NEW_PASSWORD"


def reset_password(db_path: str, username: str, password: str) -> None:
    """Set a new temporary password for `username` (policy-checked), force a change at next login, enable it, revoke sessions."""
    conn = connect(db_path)
    try:
        user = accounts.find_user(conn, username.strip().lower())
        if user is None:
            raise DomainError("user_not_found", f"no account named {username!r}", status=404)
        accounts.set_password(conn, user["user_id"], password, must_change=True)
        conn.execute("UPDATE users SET disabled = 0 WHERE user_id = ?", (user["user_id"],))
        conn.commit()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="service015-admin")
    sub = parser.add_subparsers(dest="command", required=True)
    rp = sub.add_parser("reset-password", help="set a new temporary password for an account (forces a change at next sign-in)")
    rp.add_argument("username")
    args = parser.parse_args(argv)
    password = os.environ.get(ENV_VAR)
    if not password:
        password = getpass.getpass("New temporary password: ")
        if password != getpass.getpass("Repeat: "):
            print("error: the passwords do not match", file=sys.stderr)
            return 2
    try:
        reset_password(get_settings().db_path, args.username, password)
    except DomainError as exc:
        print(f"error: {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    print(f"password for {args.username.strip().lower()} reset; the account must change it at next sign-in")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
