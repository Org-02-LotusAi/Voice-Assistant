"""Final-review fixes: LLM 429 handling, session recovery, history cap, empty-completion note, credentials, dial and upload guards."""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from livekit.agents import Agent, APIStatusError, llm

from service015 import identities_repo
from service015.api.app import create_app
from service015.config import Settings, check_api_keys_for_deployment
from service015.db import connect, migrate
from service015.runtime import presence as P
from service015.runtime import worker
from service015.runtime.latency import LatencyRecorder, purge_old_text
from service015.templates_repo import seed_starter_templates
from service015.telephony.service014 import Service014Client
from tests.conftest import H, make_app
from tests.test_llm_node_empty import _chunk, _Script
from tests.test_telephony import NUMBER, RecordingBackend, StatusSupervisor
from tests.voice_fakes import FakeVoiceStack

LK_KEY = "lk" + "a" * 14
LK_SECRET = "s" * 40
OP = "o" * 32
OP_H = {"Authorization": f"Bearer {OP}"}


def settings(**kw):
    return Settings(SERVICE015_API_KEYS="k" * 32 + ":admin", _env_file=None, **kw)


def ava(db_path):
    """Creates the database with Ava (ava-001) in STARTING and returns an open connection."""
    c = connect(str(db_path))
    migrate(c)
    seed_starter_templates(c)
    identities_repo.instantiate(c, "sales-representative", {"name": "Ava", "company": "Safi", "voice.profile_id": "vp-ava"})
    identities_repo.set_status(c, "ava-001", "STARTING")
    return c


# ---- I1: 429 classification, Retry-After, recovery state machine ----

def test_429_is_classified_rate_limited_not_unavailable():
    err429 = SimpleNamespace(type="llm_error", error=APIStatusError("slow down", status_code=429), recoverable=False)
    err500 = SimpleNamespace(type="llm_error", error=APIStatusError("boom", status_code=500), recoverable=False)
    assert worker.classify_error(err429) == (P.LLM_RATE_LIMITED, "llm")
    assert worker.classify_error(err500) == (P.LLM_UNAVAILABLE, "llm")


def test_retry_after_from_header_and_groq_message():
    exc = APIStatusError("x", status_code=429)
    exc.headers = {"retry-after": "12"}
    assert worker.retry_after_seconds(exc) == 12.0
    assert worker.retry_after_seconds(APIStatusError("Rate limit. Please try again in 1m2.5s.", status_code=429)) == 62.5
    assert worker.retry_after_seconds(APIStatusError("nothing", status_code=500)) is None


def test_session_recovery_transitions_are_bounded_and_honor_retry_after():
    r = worker.SessionRecovery(max_attempts=3, backoff_s=10.0, max_backoff_s=100.0)
    assert r.on_closed() == 10.0
    assert r.on_closed(retry_after=45.0) == 45.0  # Retry-After beats the linear backoff
    assert r.on_closed() == 30.0
    assert r.on_closed() is None and r.attempts == 3  # exhausted: ERROR stays until relaunch
    r.on_progress()  # LLM output proves recovery
    assert r.attempts == 0 and r.on_closed() == 10.0
    assert worker.SessionRecovery(2, 90.0, max_backoff_s=100.0).on_closed(retry_after=500.0) == 100.0


def test_recovery_disabled_with_zero_attempts():
    assert worker.SessionRecovery(0, 5.0).on_closed() is None


def test_new_defaults_no_preemptive_longer_endpointing_small_concurrency():
    s = settings()
    assert s.preemptive_generation is False and s.endpointing_min_delay_s == 0.6
    assert s.max_history_turns == 8 and s.max_active_identities == 2 and s.transcript_retention_days == 30


# ---- I1e: history cap; I4: lost turn recorded; rate-limited empty is not retried ----

def _agent(monkeypatch, calls, **kw):
    script = _Script(calls)
    seen = []

    async def spy(agent, chat_ctx, tools, model_settings):
        seen.append(len(chat_ctx.items))
        async for c in script(agent, chat_ctx, tools, model_settings):
            yield c

    monkeypatch.setattr(Agent.default, "llm_node", spy)
    rec = LatencyRecorder(lambda: None, "ava-001", "s")
    return worker.IdentityAgent(instructions="x", recorder=rec, **kw), script, seen


def _run(agent):
    async def go():
        ctx = llm.ChatContext.empty()
        ctx.add_message(role="system", content="instructions")
        for i in range(20):
            ctx.add_message(role="user" if i % 2 == 0 else "assistant", content=f"m{i}")
        return [c async for c in agent.llm_node(ctx, [], None)]

    return asyncio.run(go())


def test_history_is_capped_to_last_n_turns_keeping_instructions(monkeypatch):
    agent, _, seen = _agent(monkeypatch, [[_chunk("ok")]], max_history_turns=2)
    _run(agent)
    assert seen == [4 + 1]  # 2 turns = 4 messages, plus the preserved instruction message


def test_three_empty_completions_note_llm_empty_completion(monkeypatch):
    notes = []
    agent, script, _ = _agent(monkeypatch, [[_chunk("")]] * worker.MAX_EMPTY_LLM_ATTEMPTS, on_lost_turn=notes.append)
    _run(agent)
    assert script.n == worker.MAX_EMPTY_LLM_ATTEMPTS
    assert notes == [P.LLM_EMPTY_COMPLETION] == ["llm_empty_completion"]


def test_empty_completion_during_rate_limit_is_not_retried(monkeypatch):
    notes = []
    agent, script, _ = _agent(monkeypatch, [[_chunk("")]] * 3, on_lost_turn=notes.append)
    inner = Agent.default.llm_node

    async def limited(a, *args):
        a.rate_limited = True  # what the session error handler does when the provider answered 429
        async for c in inner(a, *args):
            yield c

    monkeypatch.setattr(Agent.default, "llm_node", limited)
    _run(agent)
    assert script.n == 1 and notes == []


def test_lost_turn_note_keeps_presence_and_sets_last_error(tmp_path):
    db = ava(tmp_path / "p.db")
    pres = P.PresenceController(db, "ava-001", pid=4242)
    pres.claim()
    pres.set_humans(frozenset())
    pres.note_error(P.LLM_EMPTY_COMPLETION)
    st = identities_repo.get_status(db, "ava-001")
    assert st.presence == "READY" and st.status_detail is None and st.last_error == "llm_empty_completion"


# ---- I3: retention and log levels ----

def test_purge_blanks_old_text_keeps_timings(tmp_path):
    db = ava(tmp_path / "r.db")
    for n, created in ((0, "2020-01-01T00:00:00+00:00"), (1, "2999-01-01T00:00:00+00:00")):
        db.execute(
            "INSERT INTO turn_metrics (identity_id, session_id, turn_index, t0, t1, user_text, agent_text, created_at) "
            "VALUES ('ava-001', 's', ?, 1.0, 2.0, 'secret', 'reply', ?)",
            (n, created),
        )
    db.commit()
    assert purge_old_text(db, 30) == 1
    rows = db.execute("SELECT turn_index, t0, t1, user_text, agent_text FROM turn_metrics ORDER BY turn_index").fetchall()
    assert tuple(rows[0]) == (0, 1.0, 2.0, None, None) and tuple(rows[1]) == (1, 1.0, 2.0, "secret", "reply")
    assert purge_old_text(db, 30) == 0


def test_api_startup_purges_old_turn_text(tmp_path):
    path = tmp_path / "s.db"
    db = ava(path)
    db.execute(
        "INSERT INTO turn_metrics (identity_id, session_id, turn_index, user_text, created_at) VALUES ('ava-001', 's', 0, 'secret', '2020-01-01T00:00:00+00:00')"
    )
    db.commit()
    with TestClient(make_app(path, FakeVoiceStack())):
        pass
    assert connect(str(path)).execute("SELECT user_text FROM turn_metrics").fetchone()[0] is None


def test_rotating_runtime_log_and_transcripts_not_at_info(tmp_path):
    import logging

    s = Settings(SERVICE015_DB_PATH=str(tmp_path / "d.db"), SERVICE015_RUNTIME_LOG_MAX_BYTES=2000, SERVICE015_RUNTIME_LOG_BACKUPS=2, _env_file=None)
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        worker.configure_logging(s, "DEBUG")
        logging.getLogger("service015.runtime").debug("transcript %s", "x" * 300)
        for h in root.handlers:
            h.flush()
        stream, rotating = root.handlers
        assert stream.level == logging.INFO and rotating.level == logging.DEBUG
        assert rotating.maxBytes == 2000 and rotating.backupCount == 2
        for _ in range(30):
            logging.getLogger("service015.runtime").debug("fill %s", "y" * 200)
        assert (tmp_path / "runtime-worker.rotating.log.1").exists()
    finally:
        for h in root.handlers:
            h.close()
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
    import inspect

    src = inspect.getsource(worker)
    assert 'log.info("%s: transcript' not in src and 'log.debug("%s: transcript' in src


# ---- I5: LiveKit dev credentials ----

@pytest.mark.parametrize("key,secret", [("devkey", LK_SECRET), (LK_KEY, "secret"), (LK_KEY, "short")])
def test_public_deployment_rejects_livekit_dev_credentials(key, secret):
    s = settings(SERVICE015_PUBLIC_URL="https://app.example.com", LIVEKIT_API_KEY=key, LIVEKIT_API_SECRET=secret)
    with pytest.raises(ValueError) as e:
        check_api_keys_for_deployment(s)
    assert "LIVEKIT" in str(e.value) and LK_SECRET not in str(e.value)


def test_livekit_dev_credentials_allowed_on_localhost():
    check_api_keys_for_deployment(settings(SERVICE015_PUBLIC_URL="http://localhost:8015"))


# ---- M4: POST /voices authenticates before the body is read ----

def test_unauthenticated_voice_upload_is_rejected_before_the_body(db_file, voice_stack):
    with TestClient(make_app(db_file, voice_stack)) as c:
        big = {"file": ("a.wav", b"x" * (1024 * 1024), "audio/wav")}
        r = c.post("/voices", files=big, data={"name": "n"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
        r = c.post("/voices", files=big, data={"name": "n"}, headers=H["operator"])
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
        assert c.post("/voices", files=big, data={"name": "n"}, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_voice_upload_form_is_never_parsed_when_unauthenticated(db_file, voice_stack, monkeypatch):
    import starlette.requests as sr

    called = []
    orig = sr.Request.form

    def spy(self, *a, **k):
        called.append(1)
        return orig(self, *a, **k)

    monkeypatch.setattr(sr.Request, "form", spy)
    with TestClient(make_app(db_file, voice_stack)) as c:
        assert c.post("/voices", files={"file": ("a.wav", b"x" * 100, "audio/wav")}, data={"name": "n"}).status_code == 401
    assert called == []


# ---- M6: dial allowlist and in-flight guard ----

def _dial_client(db_file, tel, **kw):
    s = Settings(
        SERVICE015_API_KEYS="k" * 32 + ":admin," + OP + ":operator", SERVICE015_DB_PATH=str(db_file),
        SERVICE014_MODE="livekit_sip", SERVICE014_SIP_TRUNK_ID="ST_x", _env_file=None, **kw,
    )
    c = TestClient(create_app(s, StatusSupervisor("READY"), tel))
    c.__enter__()
    c.post("/templates/sales-representative/instantiate", json={"overrides": {"name": "Ava", "company": "Safi"}}, headers=OP_H)
    return c


def test_allowlist_parsing_and_enforcement(db_file):
    backend = RecordingBackend()
    c = _dial_client(db_file, Service014Client("livekit_sip", backend), SERVICE014_ALLOWED_DESTINATIONS="+91, +1415")
    r = c.post("/identities/ava-001/phone/dial", json={"to": "+442071234567"}, headers=OP_H)
    assert r.status_code == 403 and r.json()["error"]["code"] == "destination_not_allowed"
    assert backend.calls == []
    assert c.post("/identities/ava-001/phone/dial", json={"to": NUMBER}, headers=OP_H).status_code == 202
    assert c.post("/identities/ava-001/phone/dial", json={"to": "+14155550100"}, headers=OP_H).status_code == 202


def test_empty_allowlist_allows_on_localhost_and_denies_on_public_url(db_file, tmp_path):
    c = _dial_client(db_file, Service014Client("livekit_sip", RecordingBackend()))
    assert c.post("/identities/ava-001/phone/dial", json={"to": NUMBER}, headers=OP_H).status_code == 202
    backend = RecordingBackend()
    c2 = _dial_client(
        tmp_path / "pub.db", Service014Client("livekit_sip", backend), SERVICE015_PUBLIC_URL="https://app.example.com",
        LIVEKIT_API_KEY=LK_KEY, LIVEKIT_API_SECRET=LK_SECRET, SERVICE015_ADMIN_PASSWORD="Pub1ic-bootstrap-pass-77",  # Phase 2: required off-localhost
    )
    r = c2.post("/identities/ava-001/phone/dial", json={"to": NUMBER}, headers=OP_H)
    assert r.status_code == 403 and r.json()["error"]["code"] == "destination_not_allowed" and backend.calls == []


def test_second_dial_while_one_is_in_flight_is_409(db_file):
    class Slow(RecordingBackend):
        async def dial(self, room, to):
            await asyncio.sleep(0.6)
            return await super().dial(room, to)

    c = _dial_client(db_file, Service014Client("livekit_sip", Slow()))
    results = []
    t = threading.Thread(target=lambda: results.append(c.post("/identities/ava-001/phone/dial", json={"to": NUMBER}, headers=OP_H)))
    t.start()
    time.sleep(0.25)
    r2 = c.post("/identities/ava-001/phone/dial", json={"to": NUMBER}, headers=OP_H)
    t.join()
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "dial_in_progress"
    assert results[0].status_code == 202
    assert c.post("/identities/ava-001/phone/dial", json={"to": NUMBER}, headers=OP_H).status_code == 202  # guard released
