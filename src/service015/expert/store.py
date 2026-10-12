"""Knowledge ingestion (documents + verified experience), provenance resolution, sessions."""

from __future__ import annotations

import fnmatch
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from service015.config import ROLES
from service015.errors import NotFound, ValidationFailed

ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}


@dataclass(frozen=True)
class Provenance:
    repo: str
    branch: str
    sha: str
    dirty: bool = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValidationFailed("provenance_unresolved", f"cannot run git in {root}: {exc}") from exc
    if proc.returncode != 0:
        raise ValidationFailed(
            "provenance_unresolved", f"`git {' '.join(args)}` failed in {root}: {proc.stderr.strip() or 'no output'}"
        )
    return proc.stdout.strip()


def resolve_provenance(root: Path) -> Provenance:
    """repo / branch / exact SHA of the working tree at `root`, via `git rev-parse`."""
    sha = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    top = Path(_git(root, "rev-parse", "--show-toplevel"))
    try:
        repo = _git(root, "config", "--get", "remote.origin.url")
    except ValidationFailed:
        repo = top.name  # no remote configured: the repository directory name is the only identity there is
    dirty = bool(_git(root, "status", "--porcelain"))  # text may differ from `sha` when the tree is dirty
    return Provenance(repo=repo, branch=branch, sha=sha, dirty=dirty)


def load_manifest(path: str | Path | None = None) -> dict[str, Any]:
    text = (
        Path(path).read_text(encoding="utf-8")
        if path is not None
        else resources.files("service015.expert").joinpath("manifest.yaml").read_text(encoding="utf-8")
    )
    manifest = yaml.safe_load(text)
    for src in manifest["sources"]:
        if not isinstance(src.get("required"), bool):
            raise ValueError(f"manifest source {src['id']!r} must say required: true|false")
        if src["min_role"] not in ROLES:
            raise ValueError(f"manifest source {src['id']!r} has unknown min_role {src['min_role']!r}")
    return manifest


def chunk_text(text: str, size: int = 800, overlap: int = 150) -> list[str]:
    """Split into ~`size`-char chunks (cut at a newline or space when possible) overlapping by ~`overlap`."""
    if size <= overlap:
        raise ValueError("chunk size must exceed overlap")
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while True:
        end = min(start + size, len(text))
        if end < len(text):
            cut = max(text.rfind("\n", start + size // 2, end), text.rfind(" ", start + size // 2, end))
            if cut > start:
                end = cut
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            return chunks
        start = max(end - overlap, start + 1)


def _excluded(rel: Path, never: dict[str, list[str]]) -> str | None:
    if rel.suffix.lower() in never.get("suffixes", []):
        return f"raw audio ({rel.suffix}) is never ingested"
    if any(part.lower() in never.get("directories", []) for part in rel.parts[:-1]):
        return "raw sample/asset directories are never ingested"
    return None


def ingest_manifest(
    conn: sqlite3.Connection,
    root: str | Path,
    manifest_path: str | Path | None = None,
    provenance: Provenance | None = None,
) -> dict[str, Any]:
    """Ingest every manifest source found under `root` as `document` chunks with provenance.

    Re-ingest replaces the `document` chunks of each ingested source and NEVER touches
    `verified_experience` rows. Missing required sources never block ingestion: they are returned in
    `missing_required` (callers warn). The manifest scope and RBAC blocks are ingested as a viewer document.
    """
    root = Path(root).resolve()
    manifest = load_manifest(manifest_path)
    prov = provenance or resolve_provenance(root)
    never = manifest.get("never_ingest", {})
    size = int(manifest.get("chunking", {}).get("size", 800))
    overlap = int(manifest.get("chunking", {}).get("overlap", 150))

    plan: list[tuple[str, str, list[str]]] = []  # (source, min_role, chunks)
    ingested: dict[str, int] = {}
    skipped: list[dict[str, str]] = []
    for src in manifest["sources"]:
        files = sorted(p for p in root.glob(src["path"]) if p.is_file())
        if not files:
            skipped.append({"source": src["path"], "reason": "not present"})
            continue
        for f in files:
            rel = f.relative_to(root)
            why = _excluded(rel, never)
            if why:
                skipped.append({"source": rel.as_posix(), "reason": why})
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ValidationFailed("source_unreadable", f"{rel.as_posix()} is not valid UTF-8: {exc}") from exc
            chunks = chunk_text(text, size, overlap)
            plan.append((rel.as_posix(), src["min_role"], chunks))
            ingested[rel.as_posix()] = len(chunks)

    scope_chunks = chunk_text(_scope_rbac_text(manifest), size, overlap)
    plan.append((SCOPE_SOURCE, "viewer", scope_chunks))
    ingested[SCOPE_SOURCE] = len(scope_chunks)

    now = _now()
    with conn:  # one transaction: replace all, or nothing
        for source, min_role, chunks in plan:
            conn.execute("DELETE FROM expert_knowledge WHERE kind = 'document' AND source = ?", (source,))
            for i, chunk in enumerate(chunks, 1):
                conn.execute(
                    "INSERT INTO expert_knowledge (kind, title, body, source, repo, branch, sha, min_role, created_at, dirty)"
                    " VALUES ('document', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (f"{source} (part {i}/{len(chunks)})", chunk, source, prov.repo, prov.branch, prov.sha, min_role, now,
                     int(prov.dirty)),
                )
    return {
        "repo": prov.repo, "branch": prov.branch, "sha": prov.sha, "dirty": prov.dirty,
        "ingested": ingested, "skipped": skipped, "missing_required": missing_required(conn, manifest_path),
    }


_META_COLS = "id, kind, title, source, repo, branch, sha, min_role, created_at, dirty"
SCOPE_SOURCE = "manifest.yaml#scope-and-rbac"


def _meta(row: sqlite3.Row) -> dict[str, Any]:
    return {**dict(row), "dirty": bool(row["dirty"])}


def _scope_rbac_text(manifest: dict[str, Any]) -> str:
    lines = [f"Service Expert scope for {manifest['name']}: {' '.join(str(manifest['scope']).split())}", "", "RBAC context permissions:"]
    lines += [f"- {role}: {desc}" for role, desc in manifest["rbac"].items()]
    never = manifest.get("never_ingest", {})
    lines += ["", "Raw voice samples are never ingested or exposed. Refused file types: "
              + ", ".join(never.get("suffixes", [])) + "; refused directories: " + ", ".join(never.get("directories", [])) + "."]
    return "\n".join(lines)


def missing_required(conn: sqlite3.Connection, manifest_path: str | Path | None = None) -> list[str]:
    """Manifest paths marked required that have no ingested document chunk."""
    have = [r["source"] for r in conn.execute("SELECT DISTINCT source FROM expert_knowledge WHERE kind = 'document'")]
    return [
        s["path"]
        for s in load_manifest(manifest_path)["sources"]
        if s["required"] and not any(fnmatch.fnmatch(h, s["path"]) for h in have)
    ]


def add_experience(
    conn: sqlite3.Connection,
    title: str,
    body: str,
    source: str,
    provenance: Provenance,
    min_role: str = "viewer",
) -> tuple[dict[str, Any], bool]:
    """Record one VERIFIED EXPERIENCE row (never replaced by a document re-ingest).

    Idempotent on (title, sha): returns (existing row, False) instead of adding a duplicate.
    """
    if min_role not in ROLES:
        raise ValidationFailed("invalid_request", f"unknown min_role {min_role!r}")
    existing = conn.execute(
        f"SELECT {_META_COLS} FROM expert_knowledge WHERE kind = 'verified_experience' AND title = ? AND sha = ?",
        (title, provenance.sha),
    ).fetchone()
    if existing is not None:
        return _meta(existing), False
    with conn:
        cur = conn.execute(
            "INSERT INTO expert_knowledge (kind, title, body, source, repo, branch, sha, min_role, created_at, dirty)"
            " VALUES ('verified_experience', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, body, source, provenance.repo, provenance.branch, provenance.sha, min_role, _now(), int(provenance.dirty)),
        )
    row = conn.execute(f"SELECT {_META_COLS} FROM expert_knowledge WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _meta(row), True


def list_metadata(conn: sqlite3.Connection, role: str) -> list[dict[str, Any]]:
    """Metadata only (never body) of the knowledge visible to `role`."""
    rows = conn.execute(f"SELECT {_META_COLS} FROM expert_knowledge ORDER BY id").fetchall()
    return [_meta(r) for r in rows if ROLE_RANK[r["min_role"]] <= ROLE_RANK[role]]


def create_session(conn: sqlite3.Connection, role: str, owner_key: str | None = None) -> str:
    """New session; `owner_key` ('user:<id>' for a human, None for a machine principal) scopes who may ask in it."""
    session_id = uuid.uuid4().hex
    with conn:
        conn.execute(
            "INSERT INTO expert_sessions (session_id, role, created_at, owner_key) VALUES (?, ?, ?, ?)",
            (session_id, role, _now(), owner_key),
        )
    return session_id


def get_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM expert_sessions WHERE session_id = ?", (session_id,)).fetchone()
    if row is None:
        raise NotFound("session_not_found", f"no expert session {session_id!r}")
    return row
