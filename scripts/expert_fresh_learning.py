"""TEST 15 (SOW 24): ingest one VERIFIED EXPERIENCE fact, ask in a FRESH session, record evidence.

Runs the real app in-process against a SEPARATE database (--db, default: a fresh temp directory, so the
configured service DB is never touched) and the live LLM (LLM_API_KEY from env/.env).

  uv run python scripts/expert_fresh_learning.py \
      --title "..." --body "..." --source "tests/...::test" \
      --question "..." --expect "7 seconds" [--expect ...]

Repo/branch/SHA come from `git rev-parse` unless --repo/--branch/--sha are given.
Writes evidence/expert_fresh_learning.json; exits 0 on PASS, 1 on FAIL, 2 when the LLM is unavailable/unconfigured.
PASS needs: the verified_experience row id itself is cited AND every --expect text appears in the answer.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", required=True)
    ap.add_argument("--body", required=True, help="the verified fact, in full")
    ap.add_argument("--source", required=True, help="where it was verified (test, log, file)")
    ap.add_argument("--question", required=True, help="a question that requires the fact")
    ap.add_argument("--expect", action="append", required=True, help="text the answer must contain (case-insensitive); repeatable")
    ap.add_argument("--repo")
    ap.add_argument("--branch")
    ap.add_argument("--sha")
    ap.add_argument("--db", help="SQLite path for this run (default: new temp dir)")
    ap.add_argument("--out", default=str(ROOT / "evidence" / "expert_fresh_learning.json"))
    args = ap.parse_args()

    from service015.api.app import create_app
    from service015.config import get_settings
    from service015.db import connect, migrate
    from service015.expert import store

    from service015.expert import qa

    db_path = args.db or str(Path(tempfile.mkdtemp(prefix="expert_fresh_")) / "expert.db")
    settings = get_settings().model_copy(update={"db_path": db_path})
    if not settings.llm_api_key:
        print("LLM_API_KEY is not set (env or .env): cannot run the live fresh-learning test", file=sys.stderr)
        return 2
    keys = {role: key for key, role in settings.api_keys.items()}
    if "admin" not in keys or "viewer" not in keys:
        print("SERVICE015_API_KEYS must define an admin and a viewer key", file=sys.stderr)
        return 2
    admin = {"Authorization": f"Bearer {keys['admin']}"}
    viewer = {"Authorization": f"Bearer {keys['viewer']}"}

    conn = connect(settings.db_path)
    try:
        migrate(conn)
        ingest = store.ingest_manifest(conn, ROOT)
    finally:
        conn.close()

    def new_session_and_ask(client: TestClient) -> tuple[str, dict]:
        sid = client.post("/expert/sessions", headers=viewer)
        sid.raise_for_status()
        session_id = sid.json()["session_id"]
        resp = client.post(f"/expert/sessions/{session_id}/ask", json={"question": args.question}, headers=viewer)
        if resp.status_code == 503:
            print(f"LLM unavailable: {resp.text}", file=sys.stderr)
            raise SystemExit(2)
        if resp.status_code != 200:
            raise SystemExit(f"ask failed: HTTP {resp.status_code} {resp.text}")
        return session_id, resp.json()

    def expectations(answer: str) -> dict[str, bool]:
        return {e: e.lower() in answer.lower() for e in args.expect}

    payload = {"title": args.title, "body": args.body, "source": args.source}
    for k in ("repo", "branch", "sha"):
        if getattr(args, k):
            payload[k] = getattr(args, k)

    with TestClient(create_app(settings)) as client:
        before_session, before = new_session_and_ask(client)  # control: the fact is not known yet
        added = client.post("/expert/experience", json=payload, headers=admin)
        if added.status_code != 201:
            raise SystemExit(f"ingest failed: HTTP {added.status_code} {added.text}")
        item = added.json()

    conn = connect(settings.db_path)
    try:
        ranked_ids = [r["id"] for r in qa.retrieve(conn, args.question, "viewer")]
    finally:
        conn.close()
    rank = ranked_ids.index(item["id"]) + 1 if item["id"] in ranked_ids else None
    print(f"retrieval rank of the verified_experience row for the question: {rank} (of top {len(ranked_ids)}); LLM asked next")
    # A brand-new app instance (as after a restart) and a NEW session: nothing but the store carries the fact.
    with TestClient(create_app(settings)) as client:
        after_session, after = new_session_and_ask(client)

    cited = [c for c in after["citations"] if c["kind"] == "verified_experience" and c["id"] == item["id"]]
    checks = expectations(after["answer"])
    passed = bool(cited) and all(checks.values()) and after_session != before_session
    evidence = {
        "test": "TEST 15 - Fresh Expert Learning (SOW 24)",
        "result": "PASS" if passed else "FAIL",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "llm": {"base_url": settings.llm_base_url, "model": settings.llm_model},
        "document_ingest": {k: ingest[k] for k in ("repo", "branch", "sha", "dirty", "missing_required")} | {"sources": len(ingest["ingested"])},
        "verified_experience": {**item, "body": args.body},
        "retrieval_rank": rank,
        "database": settings.db_path,
        "question": args.question,
        "control_before_ingest": {"session_id": before_session, "answer": before["answer"], "expectations": expectations(before["answer"])},
        "fresh_session": {
            "session_id": after_session,
            "answer": after["answer"],
            "citations": after["citations"],
            "expectations": checks,
            "cites_the_verified_experience": bool(cited),
        },
        "criteria": "PASS = the fresh session's answer contains every expected text AND cites the verified_experience row",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{evidence['result']}: {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
