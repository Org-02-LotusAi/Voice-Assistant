"""Runtime fix-round tests: error classification, job end states, staleness everywhere, reconcile, env allow-list,
request_fnc, voice-less refusal, concurrent launch, worker writer thread."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from livekit.agents import llm, stt, tts

from service015 import identities_repo
from service015.config import Settings
from service015.db import connect, migrate
from service015.participants import apply_staleness
from service015.runtime import presence as P
from service015.runtime import worker
from service015.runtime.procs import allowed_env
from service015.runtime.supervisor import LiveKitSupervisor, reconcile_runtime_lost, worker_env
from service015.templates_repo import seed_starter_templates
from tests.conftest import H, make_app, voiced_identity
from tests.test_runtime import FakeLK, FakeWorker, _llm_transport, _settings
from tests.voice_fakes import FakeVoiceStack


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "lc.db")
    migrate(c)
    seed_starter_templates(c)
    identities_repo.instantiate(c, "sales-representative", {"name": "Ava", "company": "Safi", "voice.profile_id": "vp-ava"})
    yield c
    c.close()


def _db_path(conn) -> str:
    return conn.execute("PRAGMA database_list").fetchone()[2]


def _claimed(conn, pid=11):
    identities_repo.set_status(conn, "ava-001", "STARTING")
    pres = P.PresenceController(conn, "ava-001", pid=pid)
    pres.claim()
    pres.refresh()
    return pres


# ------------------------------------------------------------------------------------------------ I1 error classification


class _Err(SimpleNamespace):
    pass


@pytest.mark.parametrize(
    "etype,detail",
    [("llm_error", "llm_unavailable"), ("stt_error", "stt_failure"), ("tts_error", "tts_unavailable")],
)
def test_classify_error_names_the_real_component(etype, detail):
    assert worker.classify_error(_Err(type=etype))[0] == detail


def test_classify_error_falls_back_to_source_type_and_never_defaults_to_llm():
    class FakeTTS(tts.TTS):
        def __init__(self):
            super().__init__(capabilities=tts.TTSCapabilities(streaming=False), sample_rate=24000, num_channels=1)

        def synthesize(self, *a, **k):  # pragma: no cover
            raise NotImplementedError

    assert worker.classify_error(_Err(type="weird"), FakeTTS())[0] == "tts_unavailable"
    assert worker.classify_error(_Err(type="weird"), object()) is None
    assert worker.classify_error(_Err(type="weird")) is None


def test_recoverable_error_only_notes_and_unrecoverable_latches_until_a_reply_completes(conn):
    pres = _claimed(conn)
    pres.note_error("tts (recovered): APIConnectionError")
    st = identities_repo.get_status(conn, "ava-001")
    assert (st.presence, st.status_detail, st.last_error) == ("READY", None, "tts (recovered): APIConnectionError")
    pres.fail("stt_failure", "stt error: boom")
    st = identities_repo.get_status(conn, "ava-001")
    assert (st.presence, st.status_detail) == ("ERROR", "stt_failure")
    pres.recovered()  # what the worker calls when a spoken reply completes
    st = identities_repo.get_status(conn, "ava-001")
    assert (st.presence, st.status_detail, st.last_error) == ("READY", None, None)
    pres.human_joined("h")
    pres.fail("tts_unavailable", "x")
    pres.recovered()
    assert identities_repo.get_status(conn, "ava-001").presence == "BUSY"


# ------------------------------------------------------------------------------------------------ I2 job end states


def test_room_disconnect_ends_as_error_livekit_disconnected_not_offline(conn):
    pres = _claimed(conn)
    assert pres.end(P.ROOM_DISCONNECTED_REASON) == "ERROR"
    st = identities_repo.get_status(conn, "ava-001")
    assert (st.presence, st.status_detail) == ("ERROR", "livekit_disconnected")


def test_reconnecting_maps_to_error_and_back(conn):
    pres = _claimed(conn)
    pres.set_reconnecting(True)
    st = identities_repo.get_status(conn, "ava-001")
    assert (st.presence, st.status_detail) == ("ERROR", "RECONNECTING")
    pres.set_reconnecting(False)
    assert identities_repo.get_status(conn, "ava-001").presence == "READY"


def test_worker_shutdown_reason_ends_offline_but_stopped_job_writes_nothing(conn):
    pres = _claimed(conn)
    assert pres.end("worker shutting down") == "OFFLINE"
    assert identities_repo.get_status(conn, "ava-001").presence == "OFFLINE"
    again = _claimed(conn, pid=12)
    identities_repo.set_status(conn, "ava-001", "OFFLINE")  # operator stop
    assert again.end(P.ROOM_DISCONNECTED_REASON) is None  # not the owner: must not turn a stop into ERROR
    assert identities_repo.get_status(conn, "ava-001").presence == "OFFLINE"


def test_terminal_startup_failure_survives_job_end(conn):
    pres = _claimed(conn)
    pres.fail("tts_unavailable", "engine could not warm", terminal=True)
    assert pres.end("tts_unavailable") == "ERROR"
    assert identities_repo.get_status(conn, "ava-001").status_detail == "tts_unavailable"


def test_relaunch_after_livekit_disconnect_rejoins_same_room_and_identity(conn):
    pres = _claimed(conn)
    pres.end(P.ROOM_DISCONNECTED_REASON)
    stack = FakeVoiceStack()
    lk = FakeLK()
    sup = LiveKitSupervisor(
        _settings(_db_path(conn)), engine=stack.engine.client, llm_transport=_llm_transport(), api_factory=lambda: lk,
        worker=FakeWorker(), worker_ready_timeout_s=2,
    )
    st = asyncio.run(sup.launch("ava-001"))
    assert (st["presence"], st["default_room"], st["participant_identity"]) == ("STARTING", "ava-001", "ava-001")
    assert ("create_dispatch", "service015-identity", "ava-001", '{"identity_id":"ava-001"}') in lk.calls


# ------------------------------------------------------------------------------------------------ I5 staleness everywhere


def test_apply_staleness_rules():
    now = datetime.now(timezone.utc)
    base = {"presence": "READY", "status_detail": None, "last_error": None, "launched_at": now.isoformat(),
            "heartbeat_at": now.isoformat()}
    assert apply_staleness(base)["presence"] == "READY"
    old = (now - timedelta(seconds=30)).isoformat()
    assert apply_staleness({**base, "heartbeat_at": old})["status_detail"] == "heartbeat_lost"
    assert apply_staleness({**base, "presence": "BUSY", "heartbeat_at": None})["presence"] == "ERROR"
    slow = {**base, "presence": "STARTING", "launched_at": (now - timedelta(seconds=200)).isoformat(), "heartbeat_at": None}
    assert apply_staleness(slow)["status_detail"] == "start_timeout"
    assert apply_staleness({**base, "presence": "OFFLINE", "heartbeat_at": None})["presence"] == "OFFLINE"


def test_roster_and_status_agree_on_a_dead_ready_identity(db_file):
    stack = FakeVoiceStack()
    with TestClient(make_app(db_file, stack)) as c:
        assert voiced_identity(c).status_code == 201
        raw = connect(db_file)
        identities_repo.set_status(raw, "ava-001", "STARTING", pid=1)
        identities_repo.set_status(raw, "ava-001", "READY")
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        raw.execute("UPDATE runtime_status SET heartbeat_at = ?", (old,))
        raw.commit()
        raw.close()
        status = c.get("/identities/ava-001/status", headers=H["viewer"]).json()
        roster = {p["identity_id"]: p for p in c.get("/meeting/participants", headers=H["viewer"]).json()}
        assert status["presence"] == roster["ava-001"]["presence"] == "ERROR"
        assert status["status_detail"] == roster["ava-001"]["status_detail"] == "heartbeat_lost"


# ------------------------------------------------------------------------------------------------ I4/I6 reconcile, env


def test_reconcile_marks_orphans_offline_runtime_lost_and_ends_sessions(conn):
    identities_repo.set_status(conn, "ava-001", "STARTING", pid=5)
    identities_repo.set_status(conn, "ava-001", "BUSY")
    conn.execute("INSERT INTO runtime_sessions (session_id, identity_id, room, started_at) VALUES ('s1','ava-001','ava-001','t')")
    conn.commit()
    assert reconcile_runtime_lost(conn) == 1
    st = identities_repo.get_status(conn, "ava-001")
    assert (st.presence, st.status_detail, st.pid) == ("OFFLINE", "runtime_lost", None)
    row = conn.execute("SELECT ended_at, end_reason FROM runtime_sessions").fetchone()
    assert row["ended_at"] and row["end_reason"] == "runtime_lost"
    assert reconcile_runtime_lost(conn) == 0


def test_supervisor_startup_reconciles_unless_a_worker_is_adopted(conn):
    class Adopting(FakeWorker):
        adopt = False

        def adopt_existing(self):
            return self.adopt

    identities_repo.set_status(conn, "ava-001", "STARTING", pid=5)
    identities_repo.set_status(conn, "ava-001", "READY")
    w = Adopting()
    sup = LiveKitSupervisor(_settings(_db_path(conn)), worker=w)
    w.adopt = True
    asyncio.run(sup.startup())
    assert identities_repo.get_status(conn, "ava-001").presence == "READY"  # a live worker keeps its jobs
    w.adopt = False
    asyncio.run(sup.startup())
    assert identities_repo.get_status(conn, "ava-001").presence == "OFFLINE"


def test_worker_env_is_an_allowlist_without_api_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("SERVICE015_API_KEYS", "supersecret:admin")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("LLM_API_KEY", "k-llm")
    env = worker_env(_settings(tmp_path / "x.db"))
    assert "SERVICE015_API_KEYS" not in env and "OPENAI_API_KEY" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert env["LLM_API_KEY"] == "k-test" and env["LIVEKIT_AGENT_NAME"] == "service015-identity"
    assert "PATH" in env or "Path" in env
    assert allowed_env({"SERVICE015_API_KEYS": "x", "LIVEKIT_URL": "u", "FOO": "1"}) == {"LIVEKIT_URL": "u"}


# ------------------------------------------------------------------------------------------------ M5 worker entry points


class FakeReq:
    def __init__(self, metadata, room):
        self.id = "job-1"
        self.job = SimpleNamespace(metadata=metadata)
        self.room = SimpleNamespace(name=room)
        self.accepted = None
        self.rejected = False

    async def accept(self, **kw):
        self.accepted = kw

    async def reject(self, **kw):
        self.rejected = True


@pytest.fixture
def worker_settings(conn, monkeypatch):
    s = _settings(_db_path(conn))
    monkeypatch.setattr(worker, "get_settings", lambda: s)
    return s


def test_request_fnc_accepts_known_identity_with_participant_identity_and_name(worker_settings):
    req = FakeReq(json.dumps({"identity_id": "ava-001"}), "ava-001")
    asyncio.run(worker.request_fnc(req))
    assert req.accepted == {"identity": "ava-001", "name": "Ava"} and not req.rejected


@pytest.mark.parametrize(
    "metadata,room",
    [("", "ava-001"), ("not json", "ava-001"), ("{}", "ava-001"), (json.dumps({"identity_id": "ghost-001"}), "ghost-001"),
     (json.dumps({"identity_id": "ava-001"}), "some-other-room")],
)
def test_request_fnc_rejects_bad_dispatches(worker_settings, metadata, room):
    req = FakeReq(metadata, room)
    asyncio.run(worker.request_fnc(req))
    assert req.rejected and req.accepted is None


def test_entrypoint_refuses_voiceless_identity_then_finish_keeps_error(conn, worker_settings):
    identities_repo.patch_identity(conn, "ava-001", {"voice.profile_id": None})
    identities_repo.set_status(conn, "ava-001", "STARTING")
    callbacks, shutdowns = [], []
    ctx = SimpleNamespace(
        job=SimpleNamespace(metadata=json.dumps({"identity_id": "ava-001"})),
        shutdown=lambda reason="": shutdowns.append(reason),
        add_shutdown_callback=callbacks.append,
        connect=lambda: pytest.fail("a voiceless identity must never connect to the room"),
    )

    async def go():
        await worker.entrypoint(ctx)
        await asyncio.sleep(0.2)  # let the queued ERROR write run on the writer thread
        for cb in callbacks:
            await cb("voice_required")

    asyncio.run(go())
    assert shutdowns == ["voice_required"]
    st = identities_repo.get_status(conn, "ava-001")
    assert (st.presence, st.status_detail) == ("ERROR", "voice_required")
    row = conn.execute("SELECT ended_at, end_reason FROM runtime_sessions").fetchone()
    assert row["ended_at"] and row["end_reason"] == "voice_required"


def test_job_writer_runs_writes_off_the_calling_thread_in_order():
    seen: list[tuple[str, str]] = []

    async def go():
        loop = asyncio.get_running_loop()
        w = P.JobWriter(loop, lambda: seen.append(("superseded", "")), "t")
        main = threading.current_thread().name
        for i in range(3):
            w.submit(lambda i=i: seen.append((str(i), threading.current_thread().name)))
        w.submit(lambda: (_ for _ in ()).throw(P.Superseded("x")))
        await w.run(lambda: None)
        await asyncio.sleep(0.05)
        w.close()
        return main

    main = asyncio.run(go())
    assert [x for x, _ in seen[:3]] == ["0", "1", "2"]
    assert all(t != main and t.startswith("db-writer-t") for _, t in seen[:3])
    assert ("superseded", "") in seen


# ------------------------------------------------------------------------------------------------ M5 concurrent launch


def test_concurrent_launches_dispatch_once(db_file):
    stack = FakeVoiceStack()
    lk = FakeLK()
    sup = LiveKitSupervisor(
        _settings(db_file), engine=stack.engine.client, llm_transport=_llm_transport(), api_factory=lambda: lk,
        worker=FakeWorker(), worker_ready_timeout_s=2,
    )
    with TestClient(make_app(db_file, stack, sup)) as c:
        assert voiced_identity(c).status_code == 201
        results: list[int] = []

        def hit():
            results.append(c.post("/identities/ava-001/launch", headers=H["operator"]).status_code)

        threads = [threading.Thread(target=hit) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert results == [200, 200]
    assert len([call for call in lk.calls if call[0] == "create_dispatch"]) == 1


# ------------------------------------------------------------------------------------------------ fix round 2: procs.py


import os
import subprocess
import sys
import time

from service015.runtime.procs import WorkerProcess, is_our_worker, read_pidfile, write_pidfile


def _wp(tmp_path) -> WorkerProcess:
    return WorkerProcess({}, tmp_path / "w.log", tmp_path / "w.pid")


def test_stale_pidfile_with_dead_pid_is_deleted_and_not_adopted(tmp_path):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    pidfile = tmp_path / "w.pid"
    pidfile.write_text(f"{dead.pid}:{time.time()}", encoding="ascii")
    w = _wp(tmp_path)
    assert w.adopt_existing() is False and w.had_pidfile is True
    assert not pidfile.exists() and w.pid is None


def test_legacy_or_garbage_pidfile_is_never_adopted(tmp_path):
    (tmp_path / "w.pid").write_text(str(os.getpid()), encoding="ascii")  # old format: pid only
    w = _wp(tmp_path)
    assert w.adopt_existing() is False
    assert not (tmp_path / "w.pid").exists()


def test_live_pid_with_wrong_create_time_or_cmdline_is_not_adopted(tmp_path):
    import psutil

    me = psutil.Process(os.getpid())
    # the pid is alive and the create_time matches, but this is pytest, not a runtime worker
    write_pidfile(tmp_path / "w.pid", os.getpid())
    w = _wp(tmp_path)
    assert w.adopt_existing() is False and not (tmp_path / "w.pid").exists()
    assert psutil.pid_exists(os.getpid())  # never bound to a job object or killed
    # a worker-looking process whose recorded create_time differs (pid reuse)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "service015.runtime.worker"])
    try:
        (tmp_path / "w.pid").write_text(f"{child.pid}:{psutil.Process(child.pid).create_time() - 3600}", encoding="ascii")
        assert not is_our_worker(child.pid, psutil.Process(child.pid).create_time() - 3600)
        assert _wp(tmp_path).adopt_existing() is False
        assert child.poll() is None  # untouched
    finally:
        child.kill()
        child.wait()
    assert me.is_running()


def test_valid_pidfile_adopts_the_live_worker(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "service015.runtime.worker"])
    try:
        write_pidfile(tmp_path / "w.pid", child.pid)
        pid, created = read_pidfile(tmp_path / "w.pid")
        assert pid == child.pid and is_our_worker(pid, created)
        w = _wp(tmp_path)
        assert w.adopt_existing() is True and w.pid == child.pid and w.alive()
        assert (tmp_path / "w.pid").exists()
    finally:
        child.kill()
        child.wait()
    assert not w.alive()


# ------------------------------------------------------------------------------------------------ fix round 2: load gate


def test_infinite_load_threshold_accepts_back_to_back_dispatches():
    """SDK-level check of AgentServer._is_available (livekit-agents 1.8.3): two requests reserved before either job appears."""
    from service015.runtime.worker import build_server

    server = build_server(Settings(_env_file=None))
    server._proc_pool = SimpleNamespace(processes=[])  # no running jobs yet
    server._devmode = False
    server._draining = False
    server._worker_load = 1.0  # a saturated host
    for reserved in (0, 1, 2, 5):
        server._reserved_slots = reserved
        assert server._is_available() is True
    # contrast: the default prod threshold declines the second dispatch even on an idle host (why it was replaced)
    default = build_server(Settings(_env_file=None))
    default._proc_pool = SimpleNamespace(processes=[])
    default._load_threshold = 0.7
    default._devmode = False
    default._draining = False
    default._worker_load = 0.0
    default._reserved_slots = 1
    assert default._is_available() is False


# ------------------------------------------------------------------------------------------------ fix round 2: capacity


def test_launch_is_refused_with_409_at_the_active_identity_limit(conn, tmp_path):
    identities_repo.instantiate(conn, "sales-representative", {"name": "James", "company": "Safi", "voice.profile_id": "vp-ava"})
    stack = FakeVoiceStack()
    lk = FakeLK()
    sup = LiveKitSupervisor(
        _settings(_db_path(conn), SERVICE015_MAX_ACTIVE_IDENTITIES=1), engine=stack.engine.client,
        llm_transport=_llm_transport(), api_factory=lambda: lk, worker=FakeWorker(), worker_ready_timeout_s=2,
    )
    assert asyncio.run(sup.launch("ava-001"))["presence"] == "STARTING"
    from service015.errors import Conflict

    with pytest.raises(Conflict) as exc:
        asyncio.run(sup.launch("james-001"))
    assert exc.value.status == 409 and exc.value.code == "capacity_exceeded"
    assert exc.value.details == {"active": 1, "limit": 1}
    assert identities_repo.get_status(conn, "james-001").presence == "OFFLINE"
    assert asyncio.run(sup.launch("ava-001"))["presence"] == "STARTING"  # idempotent relaunch is not counted twice
    asyncio.run(sup.stop("ava-001"))
    assert asyncio.run(sup.launch("james-001"))["presence"] == "STARTING"


def test_startup_leaves_rows_alone_when_this_api_never_managed_a_worker(conn):
    class NoPidfile(FakeWorker):
        had_pidfile = False

        def adopt_existing(self):
            return False

    identities_repo.set_status(conn, "ava-001", "STARTING", pid=5)
    identities_repo.set_status(conn, "ava-001", "READY")
    asyncio.run(LiveKitSupervisor(_settings(_db_path(conn)), worker=NoPidfile()).startup())
    assert identities_repo.get_status(conn, "ava-001").presence == "READY"


def test_recovery_is_scoped_to_the_component_that_succeeded(conn):
    pres = _claimed(conn)
    pres.fail(P.STT_FAILURE, "stt error: boom")
    pres.recovered(P.TTS_UNAVAILABLE)  # a spoken greeting proves TTS only
    pres.recovered(P.LLM_UNAVAILABLE)
    assert identities_repo.get_status(conn, "ava-001").status_detail == "stt_failure"
    pres.recovered(P.STT_FAILURE)  # a final transcript proves STT
    assert identities_repo.get_status(conn, "ava-001").presence == "READY"
