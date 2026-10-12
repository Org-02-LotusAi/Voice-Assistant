"""Scoped Service Expert routes."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Response

from service015.api.deps import get_conn, get_expert_llm
from service015.api.schemas import (
    AskRequest, AskResponse, ExperienceCreate, ExpertKnowledgeItem, ExpertKnowledgeList, ExpertSession, ReingestResult,
)
from service015.auth import Principal, require_principal, require_role
from service015.errors import ValidationFailed
from service015.expert import qa, store
from service015.expert.llm import Llm

router = APIRouter(tags=["expert"])


@router.post(
    "/expert/experience",
    status_code=201,
    response_model=ExpertKnowledgeItem,
    dependencies=[Depends(require_role("admin"))],
)
def add_experience(body: ExperienceCreate, response: Response, conn: sqlite3.Connection = Depends(get_conn)) -> Any:
    if body.repo and body.branch and body.sha:
        prov = store.Provenance(body.repo, body.branch, body.sha)
    elif body.repo or body.branch or body.sha:
        raise ValidationFailed("invalid_request", "give repo, branch and sha together, or none of them to resolve via git")
    else:
        prov = store.resolve_provenance(Path.cwd())
    item, created = store.add_experience(conn, body.title, body.body, body.source, prov, body.min_role)
    response.status_code = 201 if created else 200  # idempotent on (title, sha)
    return item


@router.post(
    "/expert/reingest",
    response_model=ReingestResult,
    dependencies=[Depends(require_role("admin"))],
)
def reingest(conn: sqlite3.Connection = Depends(get_conn)) -> Any:
    """Re-ingest the manifest documents from the service's working directory. Verified experience is never touched."""
    return store.ingest_manifest(conn, Path.cwd())


def _scope(principal: Principal) -> str | None:
    """Human sessions are private to their creator (admins see all); machine credentials stay global as in Phase 1."""
    return f"user:{principal.user_id}" if principal.kind == "user" and principal.role != "admin" else None


@router.get("/expert/knowledge", response_model=ExpertKnowledgeList)
def list_knowledge(
    principal: Principal = Depends(require_principal("viewer")), conn: sqlite3.Connection = Depends(get_conn)
) -> Any:
    return {"items": store.list_metadata(conn, principal.effective_role), "missing_required": store.missing_required(conn)}


@router.post("/expert/sessions", status_code=201, response_model=ExpertSession)
def create_session(
    principal: Principal = Depends(require_principal("viewer")), conn: sqlite3.Connection = Depends(get_conn)
) -> Any:
    owner_key = f"user:{principal.user_id}" if principal.kind == "user" else None
    return {"session_id": store.create_session(conn, principal.effective_role, owner_key)}


@router.post("/expert/sessions/{session_id}/ask", response_model=AskResponse)
def ask(
    session_id: str,
    body: AskRequest,
    principal: Principal = Depends(require_principal("viewer")),
    conn: sqlite3.Connection = Depends(get_conn),
    llm: Llm = Depends(get_expert_llm),
) -> Any:
    return qa.ask(conn, session_id, body.question, principal.effective_role, llm=llm, scope=_scope(principal))
