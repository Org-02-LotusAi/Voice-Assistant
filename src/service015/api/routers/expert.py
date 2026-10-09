"""Scoped Service Expert routes."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from service015.api.deps import get_conn, get_expert_llm
from service015.api.schemas import AskRequest, AskResponse, ExperienceCreate, ExpertKnowledgeItem, ExpertSession
from service015.auth import require_role
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
def add_experience(body: ExperienceCreate, conn: sqlite3.Connection = Depends(get_conn)) -> Any:
    if body.repo and body.branch and body.sha:
        prov = store.Provenance(body.repo, body.branch, body.sha)
    elif body.repo or body.branch or body.sha:
        raise ValidationFailed("invalid_request", "give repo, branch and sha together, or none of them to resolve via git")
    else:
        prov = store.resolve_provenance(Path.cwd())
    return store.add_experience(conn, body.title, body.body, body.source, prov, body.min_role)


@router.get("/expert/knowledge", response_model=list[ExpertKnowledgeItem])
def list_knowledge(role: str = Depends(require_role("viewer")), conn: sqlite3.Connection = Depends(get_conn)) -> Any:
    return store.list_metadata(conn, role)


@router.post("/expert/sessions", status_code=201, response_model=ExpertSession)
def create_session(role: str = Depends(require_role("viewer")), conn: sqlite3.Connection = Depends(get_conn)) -> Any:
    return {"session_id": store.create_session(conn, role)}


@router.post("/expert/sessions/{session_id}/ask", response_model=AskResponse)
def ask(
    session_id: str,
    body: AskRequest,
    role: str = Depends(require_role("viewer")),
    conn: sqlite3.Connection = Depends(get_conn),
    llm: Llm = Depends(get_expert_llm),
) -> Any:
    return qa.ask(conn, session_id, body.question, role, llm=llm)
