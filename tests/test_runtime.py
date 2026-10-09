"""Runtime unit tests: latency recorder/report, presence ownership, supervisor preflight/launch/stop with mocks, API routes."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from service015 import identities_repo
from service015.config import Settings
from service015.db import connect, migrate
from service015.errors import DependencyUnavailable
from service015.runtime import presence as P
from service015.runtime.latency import LatencyRecorder, percentile, report, set_t5
from service015.runtime.supervisor import LiveKitSupervisor
from service015.templates_repo import seed_starter_templates
from tests.conftest import H, make_app, voiced_identity
from tests.voice_fakes import FakeVoiceStack

# ---------------------------------------------------------------------------------------------------------------- fixtures


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "rt.db")
    migrate(c)
    seed_starter_templates(c)
    identities_repo.instantiate(
        c, "sales-representative", {"name": "Ava", "company": "Safi", "voice.profile_id": "vp-ava"}
    )
    yield c
    c.close()


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> float:
        self.t += seconds
        return self.t


# ---------------------------------------------------------------------------------------------------------------- latency


def test_percentile_nearest_rank():
    assert percentile([], 50) is None
    assert percentile([5], 95) == 5
    data = list(range(1, 101))  # 1..100
    assert percentile(data, 50) == 50
    assert percentile(data, 95) == 95
    assert percentile([30, 10, 20], 50) == 20  # unsorted input
    assert percentile([10, 20, 30, 40], 95) == 40


def _turn(rec: LatencyRecorder, clk: Clock, gaps: dict[str, float], user="what is your name", agent="My name is Ava."):
    """Drive one full turn through the recorder; gaps are the seconds between consecutive stamps."""
    rec.on_user_state("listening", "speaking")
    clk.advance(1.0)
    rec.on_user_state("speaking", "listening")  # T0
    clk.advance(gaps["t0_t1"])
    rec.on_user_transcript(user, True)  # T1
    clk.advance(gaps["t1_t2"])
    rec.on_llm_chunk(agent)  # T2
    clk.advance(gaps["t2_t3"])
    rec.on_tts_first_audio(clk())  # T3
    clk.advance(gaps["t3_t4"])
    rec.on_agent_state("thinking", "speaking")  # T4
    clk.advance(2.0)
    rec.on_agent_state("speaking", "listening")


def test_recorder_stamps_each_point_once_and_reports_percentiles(conn):
    clk = Clock()
    rec = LatencyRecorder(lambda: connect(conn.execute("PRAGMA database_list").fetchone()[2]), "ava-001", "s1", clk)
    for i in range(1, 11):  # ten turns: T0->T4 = (0.1 + 0.2 + 0.3 + 0.1*i) s
        _turn(rec, clk, {"t0_t1": 0.1, "t1_t2": 0.2, "t2_t3": 0.3, "t3_t4": 0.1 * i})
    rec.close()
    r = report(conn, "ava-001")
    assert r["turns"] == 10
    assert r["t0_t4"]["count"] == 10
    assert r["t0_t4"]["p50"] == pytest.approx(1100.0)  # values 700..1600 ms; nearest-rank 5th = 1100
    assert r["t0_t4"]["p95"] == pytest.approx(1600.0)
    assert r["t0_t4"]["max"] == pytest.approx(1600.0)
    assert r["t0_t1"]["p50"] == pytest.approx(100.0)
    assert r["t1_t2"]["p50"] == pytest.approx(200.0)
    assert r["t2_t3"]["p50"] == pytest.approx(300.0)
    assert r["t3_t4"]["max"] == pytest.approx(1000.0)
    assert r["t4_t5"] == {"count": 0, "p50": None, "p95": None, "max": None}
    row = conn.execute("SELECT * FROM turn_metrics WHERE turn_index = 0").fetchone()
    assert row["user_text"] == "what is your name" and row["agent_text"] == "My name is Ava."
    assert row["t5"] is None and row["interrupted"] == 0


def test_recorder_latches_t3_per_turn_and_ignores_greeting(conn):
    clk = Clock()
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    rec = LatencyRecorder(lambda: connect(path), "ava-001", "s1", clk)
    # greeting: agent speaks with no user turn open -> nothing recorded
    rec.on_tts_first_audio(clk())
    rec.on_agent_state("idle", "speaking")
    rec.on_agent_state("speaking", "listening")
    assert conn.execute("SELECT COUNT(*) FROM turn_metrics").fetchone()[0] == 0

    rec.on_user_state("listening", "speaking")
    clk.advance(1)
    rec.on_user_state("speaking", "listening")  # T0
    rec.on_user_transcript("hello", True)
    rec.on_tts_first_audio(clk())  # before T2: must not latch
    clk.advance(0.5)
    rec.on_llm_chunk("Hi")  # T2
    first_t2 = clk()
    clk.advance(0.5)
    rec.on_llm_chunk(" there.")  # must not move T2
    clk.advance(0.5)
    rec.on_tts_first_audio(clk())  # T3 (sentence 1)
    t3 = clk()
    clk.advance(5)
    rec.on_tts_first_audio(clk())  # sentence 2: must not move T3
    rec.on_agent_state("thinking", "speaking")
    rec.close()
    row = conn.execute("SELECT * FROM turn_metrics").fetchone()
    assert row["t2"] == first_t2 and row["t3"] == t3
    assert row["agent_text"] == "Hi there."


def test_recorder_marks_interrupted_and_report_scopes_by_session(conn):
    clk = Clock()
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    rec = LatencyRecorder(lambda: connect(path), "ava-001", "s-a", clk)
    _turn(rec, clk, {"t0_t1": 0.1, "t1_t2": 0.1, "t2_t3": 0.1, "t3_t4": 0.1})
    rec.on_agent_state("listening", "speaking")  # agent starts a long answer ...
    rec.on_user_state("listening", "speaking")  # ... and the user talks over it
    rec.close()
    rows = conn.execute("SELECT turn_index, interrupted FROM turn_metrics ORDER BY turn_index").fetchall()
    assert [(r["turn_index"], r["interrupted"]) for r in rows] == [(0, 1)]
    other = LatencyRecorder(lambda: connect(path), "ava-001", "s-b", clk)
    _turn(other, clk, {"t0_t1": 0.1, "t1_t2": 0.1, "t2_t3": 0.1, "t3_t4": 0.1})
    other.close()
    assert report(conn, "ava-001")["turns"] == 2
    assert report(conn, "ava-001", "s-b")["turns"] == 1
    assert report(conn, "nobody-001")["turns"] == 0


def test_set_t5_fills_interval(conn):
    clk = Clock()
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    rec = LatencyRecorder(lambda: connect(path), "ava-001", "s1", clk)
    _turn(rec, clk, {"t0_t1": 0.1, "t1_t2": 0.1, "t2_t3": 0.1, "t3_t4": 0.1})
    rec.close()
    row = conn.execute("SELECT id, t4 FROM turn_metrics").fetchone()
    set_t5(conn, row["id"], row["t4"] + 0.25)
    assert report(conn, "ava-001")["t4_t5"]["p50"] == pytest.approx(250.0)
    with pytest.raises(LookupError):
        set_t5(conn, 9999, 1.0)


# ---------------------------------------------------------------------------------------------------------------- presence


def test_presence_ready_busy_error_and_ownership(conn):
    identities_repo.set_status(conn, "ava-001", "STARTING")
    pres = P.PresenceController(conn, "ava-001", pid=4242)
    pres.claim()
    assert identities_repo.get_status(conn, "ava-001").pid == 4242
    pres.refresh()
    st = identities_repo.get_status(conn, "ava-001")
    assert (st.presence, st.heartbeat_at is not None) == ("READY", True)
    pres.human_joined("human-1")
    assert identities_repo.get_status(conn, "ava-001").presence == "BUSY"
    pres.set_reconnecting(True)
    st = identities_repo.get_status(conn, "ava-001")
    assert (st.presence, st.status_detail) == ("ERROR", "RECONNECTING")
    pres.set_reconnecting(False)
    assert identities_repo.get_status(conn, "ava-001").presence == "BUSY"
    pres.fail(P.LLM_UNAVAILABLE, "llm error: boom")
    st = identities_repo.get_status(conn, "ava-001")
    assert (st.presence, st.status_detail, st.last_error) == ("ERROR", "llm_unavailable", "llm error: boom")
    pres.recovered(P.STT_FAILURE)  # a different component recovering does not clear it
    assert identities_repo.get_status(conn, "ava-001").presence == "ERROR"
    pres.recovered(P.LLM_UNAVAILABLE)
    pres.human_left("human-1")
    assert identities_repo.get_status(conn, "ava-001").presence == "READY"
    assert pres.release() is True
    assert identities_repo.get_status(conn, "ava-001").presence == "OFFLINE"


def test_stopped_identity_cannot_be_resurrected_by_stale_job(conn):
    identities_repo.set_status(conn, "ava-001", "STARTING")
    pres = P.PresenceController(conn, "ava-001", pid=1)
    pres.claim()
    identities_repo.set_status(conn, "ava-001", "OFFLINE")  # supervisor.stop()
    with pytest.raises(P.Superseded):
        pres.refresh()
    with pytest.raises(P.Superseded):
        pres.heartbeat()
    assert pres.release() is False
    assert identities_repo.get_status(conn, "ava-001").presence == "OFFLINE"
    # a job that starts after the stop must not claim the row either
    late = P.PresenceController(conn, "ava-001", pid=2)
    with pytest.raises(P.Superseded):
        late.claim()


def test_relaunch_supersedes_old_job(conn):
    identities_repo.set_status(conn, "ava-001", "STARTING")
    old = P.PresenceController(conn, "ava-001", pid=1)
    old.claim()
    identities_repo.set_status(conn, "ava-001", "OFFLINE")
    identities_repo.set_status(conn, "ava-001", "STARTING")
    new = P.PresenceController(conn, "ava-001", pid=2)
    new.claim()
    with pytest.raises(P.Superseded):
        old.heartbeat()
    assert old.release() is False
    assert identities_repo.get_status(conn, "ava-001").pid == 2


# ---------------------------------------------------------------------------------------------------------------- supervisor


class FakeWorker:
    def __init__(self) -> None:
        self.spawned = 0
        self.stopped = 0
        self.pid: int | None = None
        self._alive = False

    def alive(self) -> bool:
        return self._alive

    def registered(self) -> bool:
        return self._alive

    def spawn(self) -> None:
        self.spawned += 1
        self.pid, self._alive = 999, True

    def stop(self) -> None:
        self.stopped += 1
        self._alive = False
        self.pid = None

    def log_text(self) -> str:
        return "fake worker log"


class FakeLK:
    """Stand-in for livekit.api.LiveKitAPI recording calls."""

    def __init__(self, reachable: bool = True) -> None:
        self.reachable = reachable
        self.calls: list[tuple] = []
        self.dispatches: list[SimpleNamespace] = []
        self.room = SimpleNamespace(
            list_rooms=self._list_rooms, create_room=self._create_room, remove_participant=self._remove_participant
        )
        self.agent_dispatch = SimpleNamespace(
            create_dispatch=self._create_dispatch, list_dispatch=self._list_dispatch, delete_dispatch=self._delete_dispatch
        )

    async def _list_rooms(self, req):
        if not self.reachable:
            raise ConnectionError("down")
        self.calls.append(("list_rooms",))

    async def _create_room(self, req):
        self.calls.append(("create_room", req.name))

    async def _remove_participant(self, req):
        self.calls.append(("remove_participant", req.room, req.identity))

    async def _create_dispatch(self, req):
        self.calls.append(("create_dispatch", req.agent_name, req.room, req.metadata))
        self.dispatches.append(SimpleNamespace(id=f"d{len(self.dispatches)}", agent_name=req.agent_name))

    async def _list_dispatch(self, room):
        return list(self.dispatches)

    async def _delete_dispatch(self, dispatch_id, room):
        self.calls.append(("delete_dispatch", dispatch_id, room))
        self.dispatches = [d for d in self.dispatches if d.id != dispatch_id]

    async def aclose(self):
        pass

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


def _settings(db_path, **kw) -> Settings:
    return Settings(
        SERVICE015_API_KEYS="adm:admin", SERVICE015_DB_PATH=str(db_path), LLM_API_KEY=kw.pop("llm_key", "k-test"),
        LLM_BASE_URL="http://llm.test/v1", _env_file=None, **kw,
    )


def _llm_transport(status: int = 200):
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(status, json={"data": []})

    t = httpx.MockTransport(handle)
    t.seen = seen  # type: ignore[attr-defined]
    return t


@pytest.fixture
def stack():
    return FakeVoiceStack()


def make_supervisor(conn, stack, lk=None, llm_status=200, llm_key="k-test", worker=None):
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    lk = lk or FakeLK()
    worker = worker or FakeWorker()
    sup = LiveKitSupervisor(
        _settings(path, llm_key=llm_key), engine=stack.engine.client, llm_transport=_llm_transport(llm_status),
        api_factory=lambda: lk, worker=worker, worker_ready_timeout_s=2,
    )
    return sup, lk, worker


def _untouched(conn):
    st = identities_repo.get_status(conn, "ava-001")
    return (st.presence, st.status_detail, st.pid, st.launched_at, st.heartbeat_at, st.last_error)


@pytest.mark.parametrize(
    "case,dependency",
    [("livekit_down", "livekit"), ("llm_no_key", "llm"), ("llm_401", "llm"), ("engine_down", "voice_engine"),
     ("engine_loading", "voice_engine"), ("warm_503", "voice_engine"), ("warm_voicebox", "voicebox")],
)
def test_preflight_failure_leaves_identity_untouched(conn, stack, case, dependency):
    kw: dict = {}
    if case == "llm_no_key":
        kw["llm_key"] = ""
    if case == "llm_401":
        kw["llm_status"] = 401
    lk = FakeLK(reachable=case != "livekit_down")
    worker = FakeWorker()
    sup, lk, worker = make_supervisor(conn, stack, lk=lk, worker=worker, **kw)
    if case == "engine_down":
        stack.engine.down = True
    if case == "engine_loading":
        stack.engine.status = "loading"
    if case == "warm_503":
        stack.engine.fail_with = (503, {"code": "engine_loading", "detail": "x"})
    if case == "warm_voicebox":
        stack.engine.fail_with = (503, {"code": "voicebox_unavailable", "detail": "x"})
    before = _untouched(conn)
    with pytest.raises(DependencyUnavailable) as exc:
        asyncio.run(sup.launch("ava-001"))
    assert exc.value.code == "dependency_unavailable"
    assert exc.value.details["dependency"] == dependency
    assert _untouched(conn) == before == ("OFFLINE", None, None, None, None, None)
    assert worker.spawned == 0
    assert not {"create_room", "create_dispatch"} & set(lk.names())


def test_preflight_probes_llm_models_with_bearer(conn, stack):
    sup, lk, _ = make_supervisor(conn, stack)
    asyncio.run(sup.launch("ava-001"))
    req = sup._llm_transport.seen[0]  # type: ignore[attr-defined]
    assert (req.method, str(req.url)) == ("GET", "http://llm.test/v1/models")
    assert req.headers["authorization"] == "Bearer k-test"


def test_launch_dispatches_with_identity_metadata_and_is_idempotent(conn, stack):
    sup, lk, worker = make_supervisor(conn, stack)
    st = asyncio.run(sup.launch("ava-001"))
    assert st["presence"] == "STARTING" and st["default_room"] == "ava-001" == st["participant_identity"]
    assert worker.spawned == 1
    disp = [c for c in lk.calls if c[0] == "create_dispatch"]
    assert disp == [("create_dispatch", "service015-identity", "ava-001", '{"identity_id":"ava-001"}')]
    assert ("create_room", "ava-001") in lk.calls
    again = asyncio.run(sup.launch("ava-001"))
    assert again["presence"] == "STARTING"
    assert len([c for c in lk.calls if c[0] == "create_dispatch"]) == 1  # no second dispatch
    assert worker.spawned == 1


def test_launch_without_voice_is_refused_before_preflight(conn, stack):
    identities_repo.patch_identity(conn, "ava-001", {"voice.profile_id": None})
    sup, lk, worker = make_supervisor(conn, stack)
    from service015.errors import DomainError

    with pytest.raises(DomainError) as exc:
        asyncio.run(sup.launch("ava-001"))
    assert exc.value.code == "voice_required" and worker.spawned == 0 and lk.calls == []


def test_stop_removes_dispatch_and_participant_and_goes_offline(conn, stack):
    sup, lk, _ = make_supervisor(conn, stack)
    asyncio.run(sup.launch("ava-001"))
    st = asyncio.run(sup.stop("ava-001"))
    assert st["presence"] == "OFFLINE" and st["launched_at"] is None
    assert ("delete_dispatch", "d0", "ava-001") in lk.calls
    assert ("remove_participant", "ava-001", "ava-001") in lk.calls
    relaunch = asyncio.run(sup.launch("ava-001"))
    assert (relaunch["participant_identity"], relaunch["default_room"]) == ("ava-001", "ava-001")


def test_status_staleness_rules(conn, stack):
    sup, _, worker = make_supervisor(conn, stack)
    identities_repo.set_status(conn, "ava-001", "STARTING", pid=7)
    identities_repo.set_status(conn, "ava-001", "READY")
    assert asyncio.run(sup.status("ava-001"))["status_detail"] == "heartbeat_lost"  # READY but never beat
    identities_repo.heartbeat(conn, "ava-001")
    assert asyncio.run(sup.status("ava-001"))["presence"] == "READY"
    old = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    conn.execute("UPDATE runtime_status SET heartbeat_at = ? WHERE identity_id = 'ava-001'", (old,))
    conn.commit()
    st = asyncio.run(sup.status("ava-001"))
    assert (st["presence"], st["status_detail"]) == ("ERROR", "heartbeat_lost")
    assert identities_repo.get_status(conn, "ava-001").presence == "READY"  # status() never writes
    identities_repo.heartbeat(conn, "ava-001")
    worker.pid, worker._alive = 5, False  # a worker we spawned has died
    assert asyncio.run(sup.status("ava-001"))["status_detail"] == "worker_exited"


def test_stale_ready_identity_can_be_relaunched(conn, stack):
    sup, lk, _ = make_supervisor(conn, stack)
    identities_repo.set_status(conn, "ava-001", "STARTING", pid=7)
    identities_repo.set_status(conn, "ava-001", "READY")  # no heartbeat -> stale
    st = asyncio.run(sup.launch("ava-001"))
    assert st["presence"] == "STARTING"
    assert identities_repo.get_status(conn, "ava-001").pid is None  # stale owner dropped


def test_ensure_worker_times_out_to_runtime_unavailable(conn, stack):
    class Mute(FakeWorker):
        def registered(self) -> bool:
            return False

    sup, _, worker = make_supervisor(conn, stack, worker=Mute())
    with pytest.raises(DependencyUnavailable) as exc:
        asyncio.run(sup.ensure_worker())
    assert exc.value.code == "runtime_unavailable" and worker.stopped == 1


# ---------------------------------------------------------------------------------------------------------------- API


def test_join_token_and_latency_routes(db_file):
    stack = FakeVoiceStack()
    with TestClient(make_app(db_file, stack)) as c:
        assert voiced_identity(c).status_code == 201
        assert c.post("/identities/ava-001/join-token", headers=H["viewer"]).status_code == 403
        assert c.post("/identities/nobody-001/join-token", headers=H["operator"]).status_code == 404
        r = c.post("/identities/ava-001/join-token", json={"participant_name": "Sam"}, headers=H["operator"])
        assert r.status_code == 200
        body = r.json()
        assert body["room"] == "ava-001" and body["livekit_url"] == "ws://localhost:7880"
        assert body["participant_identity"].startswith("human-") and len(body["participant_identity"]) == 14
        claims = jwt.decode(body["token"], "secret", algorithms=["HS256"], options={"verify_aud": False})
        assert claims["sub"] == body["participant_identity"] and claims["name"] == "Sam"
        assert claims["video"]["room"] == "ava-001" and claims["video"]["roomJoin"] is True
        r2 = c.post("/identities/ava-001/join-token", headers=H["operator"])  # body optional
        assert r2.status_code == 200 and r2.json()["participant_identity"] != body["participant_identity"]

        lat = c.get("/identities/ava-001/latency", headers=H["viewer"])
        assert lat.status_code == 200 and lat.json()["turns"] == 0 and lat.json()["t0_t4"]["p50"] is None
        assert c.get("/identities/nobody-001/latency", headers=H["viewer"]).status_code == 404


def test_create_app_wires_livekit_supervisor_only_when_configured(tmp_path, monkeypatch):
    from service015.api.app import create_app
    from service015.runtime.supervisor_protocol import NullSupervisor

    for var in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.delenv(var, raising=False)
    plain = Settings(SERVICE015_DB_PATH=str(tmp_path / "a.db"), _env_file=None)
    assert isinstance(create_app(plain).state.supervisor, NullSupervisor)
    configured = Settings(
        SERVICE015_DB_PATH=str(tmp_path / "b.db"), LIVEKIT_URL="ws://localhost:7880", LIVEKIT_API_KEY="devkey",
        LIVEKIT_API_SECRET="secret", _env_file=None,
    )
    assert isinstance(create_app(configured).state.supervisor, LiveKitSupervisor)
