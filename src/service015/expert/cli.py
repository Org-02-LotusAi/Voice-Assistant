"""`service015-expert ingest|ask` command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from service015.config import get_settings
from service015.db import connect, migrate
from service015.errors import DomainError
from service015.expert import qa, store
from service015.expert.llm import OpenAICompatLlm


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="service015-expert", description="Scoped Service 015 expert")
    sub = p.add_subparsers(dest="command", required=True)
    ing = sub.add_parser("ingest", help="(re-)ingest the manifest sources as document knowledge")
    ing.add_argument("--root", default=".", help="repository root holding the manifest sources (default: cwd)")
    ing.add_argument("--manifest", default=None, help="manifest path (default: the packaged manifest.yaml)")
    ask = sub.add_parser("ask", help="ask one question in a fresh session")
    ask.add_argument("question")
    ask.add_argument("--role", choices=["viewer", "operator", "admin"], default="viewer")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        if args.command == "ingest":
            result = store.ingest_manifest(conn, Path(args.root), args.manifest)
        else:
            sid = store.create_session(conn, args.role)
            result = {"session_id": sid, **qa.ask(conn, sid, args.question, args.role, llm=OpenAICompatLlm.from_settings(settings))}
    except DomainError as exc:
        print(f"error: {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    if args.command == "ingest" and result["missing_required"]:
        print("warning: required sources missing: " + ", ".join(result["missing_required"]), file=sys.stderr)
    if args.command == "ingest" and result["dirty"]:
        print("warning: working tree is dirty; ingested text may differ from the recorded SHA", file=sys.stderr)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
