"""Template storage with immutable versions, plus starter-template seeding."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from importlib import resources

import yaml
from pydantic import ValidationError

from service015.errors import Conflict, CorruptTemplate, NotFound, ValidationFailed
from service015.models import TemplateBody, TemplateCreate, TemplateSummary
from service015.scope import Visibility


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


def _parse_body(raw: str, template_id: str) -> TemplateBody:
    try:
        return TemplateBody.model_validate_json(raw)
    except ValidationError as exc:
        raise CorruptTemplate(
            "corrupt_template", f"stored template {template_id!r} is corrupt: {exc}", details={"template_id": template_id}
        ) from exc


def _insert_version(
    conn: sqlite3.Connection,
    template: TemplateCreate,
    only_if_absent: bool = False,
    owner_user_id: str | None = None,
    shared: bool = True,
    authorize: Callable[[sqlite3.Row | None], None] | None = None,
) -> TemplateBody | None:
    """Insert v1 or the next version in one transaction; prior version rows are never touched.

    `authorize` is called inside the write transaction with the family's metadata row (None for a new family); it raises to
    refuse, so the ownership check cannot race with a concurrent creation of the same template_id.

    With only_if_absent the existence check happens inside the write transaction and None is
    returned when the template already exists. Requires no transaction open on `conn`.
    """
    now = _now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if only_if_absent and conn.execute(
            "SELECT 1 FROM templates WHERE template_id = ?", (template.template_id,)
        ).fetchone():
            conn.rollback()
            return None
        if authorize is not None:
            authorize(
                conn.execute(
                    "SELECT template_id, owner_user_id, shared, disabled FROM templates WHERE template_id = ?",
                    (template.template_id,),
                ).fetchone()
            )
        row = conn.execute(
            "SELECT MAX(version) AS v FROM template_versions WHERE template_id = ?", (template.template_id,)
        ).fetchone()
        version = (row["v"] or 0) + 1
        if version == 1:
            conn.execute(
                "INSERT INTO templates (template_id, created_at, owner_user_id, shared) VALUES (?, ?, ?, ?)",
                (template.template_id, now, owner_user_id, int(shared)),
            )
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
        if _insert_version(conn, starter, only_if_absent=True) is not None:
            seeded.append(starter.template_id)
    return seeded


def create_template(
    conn: sqlite3.Connection,
    template: TemplateCreate,
    owner_user_id: str | None = None,
    shared: bool = True,
    authorize: Callable[[sqlite3.Row | None], None] | None = None,
) -> TemplateBody:
    """New template_id -> v1 (owned by `owner_user_id`, shared or private); existing template_id -> next version, ownership kept."""
    body = _insert_version(conn, template, owner_user_id=owner_user_id, shared=shared, authorize=authorize)
    assert body is not None  # only_if_absent=False always inserts or raises
    return body


def list_templates(conn: sqlite3.Connection, vis: Visibility | None = None) -> list[TemplateSummary]:
    rows = conn.execute(
        """
        SELECT v.template_id, v.body_json, t.owner_user_id, t.shared, t.disabled
        FROM template_versions v
        JOIN (SELECT template_id, MAX(version) AS mv FROM template_versions GROUP BY template_id) m
          ON m.template_id = v.template_id AND m.mv = v.version
        JOIN templates t ON t.template_id = v.template_id
        ORDER BY v.template_id
        """
    ).fetchall()
    summaries: list[TemplateSummary] = []
    for r in rows:
        if vis is not None and not vis.allows(r["owner_user_id"], bool(r["shared"])):
            continue
        body = _parse_body(r["body_json"], r["template_id"])
        summaries.append(
            TemplateSummary(
                template_id=body.template_id,
                latest_version=body.version,
                name=body.name,
                description=body.description,
                shared=bool(r["shared"]),
                disabled=bool(r["disabled"]),
                owner_user_id=r["owner_user_id"],
            )
        )
    return summaries


def template_meta(conn: sqlite3.Connection, template_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT template_id, owner_user_id, shared, disabled FROM templates WHERE template_id = ?", (template_id,)
    ).fetchone()
    if row is None:
        raise NotFound("template_not_found", f"template {template_id!r} not found", details={"template_id": template_id})
    return row


def ensure_visible(conn: sqlite3.Connection, template_id: str, vis: Visibility) -> sqlite3.Row:
    """Template metadata, or NotFound when the principal may not see it (indistinguishable from a missing template)."""
    meta = template_meta(conn, template_id)
    if not vis.allows(meta["owner_user_id"], bool(meta["shared"])):
        raise NotFound("template_not_found", f"template {template_id!r} not found", details={"template_id": template_id})
    return meta


def ensure_enabled(conn: sqlite3.Connection, template_id: str) -> None:
    """Disabled templates cannot be instantiated (existing identities are unaffected)."""
    if template_meta(conn, template_id)["disabled"]:
        raise Conflict(
            "template_disabled", f"template {template_id!r} is disabled and cannot be instantiated",
            details={"template_id": template_id},
        )


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
    return _parse_body(row["body_json"], template_id)


def list_versions(conn: sqlite3.Connection, template_id: str) -> list[int]:
    rows = conn.execute(
        "SELECT version FROM template_versions WHERE template_id = ? ORDER BY version", (template_id,)
    ).fetchall()
    if not rows:
        raise NotFound("template_not_found", f"template {template_id!r} not found", details={"template_id": template_id})
    return [r["version"] for r in rows]


def patch_template(
    conn: sqlite3.Connection, template_id: str, changes: dict[str, bool | str | None]
) -> TemplateSummary:
    """Admin metadata changes on a template family: `disabled`, `shared`, `owner_user_id` (only keys present are applied)."""
    template_meta(conn, template_id)
    sets, params = [], []
    for key in ("disabled", "shared"):
        if key in changes:
            sets.append(f"{key} = ?")
            params.append(int(bool(changes[key])))
    if "owner_user_id" in changes:
        sets.append("owner_user_id = ?")
        params.append(changes["owner_user_id"])
    if sets:
        conn.execute(f"UPDATE templates SET {', '.join(sets)} WHERE template_id = ?", (*params, template_id))
        conn.commit()
    return next(t for t in list_templates(conn) if t.template_id == template_id)
