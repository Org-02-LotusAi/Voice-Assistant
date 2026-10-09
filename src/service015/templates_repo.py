"""Template storage with immutable versions, plus starter-template seeding."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from importlib import resources

import yaml
from pydantic import ValidationError

from service015.errors import NotFound, ValidationFailed
from service015.models import TemplateBody, TemplateCreate, TemplateSummary


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_starters() -> list[TemplateCreate]:
    """Parse every packaged starter YAML. Invalid starter content fails loudly."""
    files = sorted(
        (f for f in resources.files("service015").joinpath("starter_templates").iterdir() if f.name.endswith(".yaml")),
        key=lambda f: f.name,
    )
    starters: list[TemplateCreate] = []
    for f in files:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        try:
            starters.append(TemplateCreate.model_validate(data))
        except ValidationError as exc:
            raise ValidationFailed("invalid_starter_template", f"starter template {f.name} is invalid: {exc}") from exc
    return starters


def _insert_version(conn: sqlite3.Connection, template: TemplateCreate) -> TemplateBody:
    """Insert v1 or the next version in one transaction; prior version rows are never touched."""
    now = _now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT MAX(version) AS v FROM template_versions WHERE template_id = ?", (template.template_id,)
        ).fetchone()
        version = (row["v"] or 0) + 1
        if version == 1:
            conn.execute("INSERT INTO templates (template_id, created_at) VALUES (?, ?)", (template.template_id, now))
        body = TemplateBody(**template.model_dump(), version=version, created_at=now)
        conn.execute(
            "INSERT INTO template_versions (template_id, version, body_json, created_at) VALUES (?, ?, ?, ?)",
            (template.template_id, version, body.model_dump_json(), now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return body


def seed_starter_templates(conn: sqlite3.Connection) -> list[str]:
    """Insert v1 of each starter whose template_id does not exist yet. Returns the ids seeded now."""
    seeded: list[str] = []
    for starter in _load_starters():
        exists = conn.execute("SELECT 1 FROM templates WHERE template_id = ?", (starter.template_id,)).fetchone()
        if exists:
            continue
        _insert_version(conn, starter)
        seeded.append(starter.template_id)
    return seeded


def create_template(conn: sqlite3.Connection, template: TemplateCreate) -> TemplateBody:
    """New template_id -> v1; existing template_id -> next version."""
    return _insert_version(conn, template)


def list_templates(conn: sqlite3.Connection) -> list[TemplateSummary]:
    rows = conn.execute(
        """
        SELECT v.body_json
        FROM template_versions v
        JOIN (SELECT template_id, MAX(version) AS mv FROM template_versions GROUP BY template_id) m
          ON m.template_id = v.template_id AND m.mv = v.version
        ORDER BY v.template_id
        """
    ).fetchall()
    summaries: list[TemplateSummary] = []
    for r in rows:
        body = TemplateBody.model_validate_json(r["body_json"])
        summaries.append(
            TemplateSummary(
                template_id=body.template_id, latest_version=body.version, name=body.name, description=body.description
            )
        )
    return summaries


def get_template(conn: sqlite3.Connection, template_id: str, version: int | None = None) -> TemplateBody:
    if version is None:
        row = conn.execute(
            "SELECT body_json FROM template_versions WHERE template_id = ? ORDER BY version DESC LIMIT 1", (template_id,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT body_json FROM template_versions WHERE template_id = ? AND version = ?", (template_id, version)
        ).fetchone()
    if row is None:
        what = f"template {template_id!r}" if version is None else f"template {template_id!r} version {version}"
        raise NotFound("template_not_found", f"{what} not found", details={"template_id": template_id, "version": version})
    return TemplateBody.model_validate_json(row["body_json"])


def list_versions(conn: sqlite3.Connection, template_id: str) -> list[int]:
    rows = conn.execute(
        "SELECT version FROM template_versions WHERE template_id = ? ORDER BY version", (template_id,)
    ).fetchall()
    if not rows:
        raise NotFound("template_not_found", f"template {template_id!r} not found", details={"template_id": template_id})
    return [r["version"] for r in rows]
