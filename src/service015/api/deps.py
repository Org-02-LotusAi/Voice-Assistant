"""Request-scoped dependencies."""

from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Request

from service015.db import connect
from service015.expert.llm import Llm
from service015.telephony.service014 import Service014Client
from service015.runtime.supervisor_protocol import RuntimeSupervisor
from service015.voice.engine import VoiceEngineClient
from service015.voice.voicebox import VoiceboxClient


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """One SQLite connection per request, always closed."""
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_telephony(request: Request) -> Service014Client:
    return request.app.state.telephony


def get_supervisor(request: Request) -> RuntimeSupervisor:
    return request.app.state.supervisor


def get_expert_llm(request: Request) -> Llm:
    return request.app.state.expert_llm


def get_voicebox(request: Request) -> VoiceboxClient:
    return request.app.state.voicebox


def get_voice_engine(request: Request) -> VoiceEngineClient:
    return request.app.state.voice_engine
