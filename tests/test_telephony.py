import asyncio
import json
import logging
import re
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from livekit import api
from livekit.api.twirp_client import ServerError

from tests.conftest import H, FakeSupervisor, make_settings
from service015.api.app import create_app
from service015.config import Settings
from service015.errors import DomainError
from service015.telephony.service014 import (
    HttpBackend,
    LiveKitSipBackend,
    Service014Client,
    TelephonyFailed,
    mask_number,
)
from tests.test_openapi_contract import assert_conforms

NUMBER = "+919876543210"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class StatusSupervisor(FakeSupervisor):
    def __init__(self, presence):
        super().__init__()
        self.presence = presence

    async def status(self, identity_id):
        return self._status(identity_id, self.presence)


class RecordingBackend:
    def __init__(self):
        self.calls = []

    async def dial(self, room, to):
        self.calls.append((room, to))
        return {"call_id": "call-1", "status": "answered"}


def app_client(db_file, presence="READY", tel=None):
    app = create_app(make_settings(db_file), StatusSupervisor(presence), tel)
    c = TestClient(app)
    c.__enter__()
    c.post("/templates/sales-representative/instantiate",
           json={"overrides": {"name": "Ava", "company": "Safi"}}, headers=H["operator"])
    return c


def sip_settings(db_file, **kw):
    return Settings(SERVICE015_API_KEYS="adm:admin,ope:operator,vie:viewer", SERVICE015_DB_PATH=str(db_file),
                    SERVICE014_MODE="livekit_sip", SERVICE014_SIP_TRUNK_ID="ST_x", _env_file=None, **kw)


def test_mask_number():
    assert mask_number(NUMBER) == "*********3210"


def test_disabled_dial_is_503_and_info_has_no_inbound(db_file):
    c = app_client(db_file)
    r = c.post("/identities/ava-001/phone/dial", json={"to": NUMBER}, headers=H["operator"])
    assert r.status_code == 503 and r.json()["error"]["code"] == "telephony_not_configured"
    assert_conforms("/identities/{identity_id}/phone/dial", "post", r)
    r = c.get("/identities/ava-001/phone", headers=H["viewer"])
    assert r.status_code == 200 and r.json() == {"mode": "disabled", "default_room": "ava-001", "inbound": None}
    assert_conforms("/identities/{identity_id}/phone", "get", r)


@pytest.mark.parametrize("bad", ["9876543210", "+0123456", "+91 98765 43210", "+1", "+1234567890123456", "", "+91abc"])
def test_dial_rejects_non_e164(db_file, bad):
    c = app_client(db_file, tel=Service014Client("livekit_sip", RecordingBackend()))
    r = c.post("/identities/ava-001/phone/dial", json={"to": bad}, headers=H["operator"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert_conforms("/identities/{identity_id}/phone/dial", "post", r)
    assert bad not in r.text or bad == ""


def test_client_rejects_bad_number_directly():
    with pytest.raises(DomainError) as e:
        asyncio.run(Service014Client("http", RecordingBackend()).connect_phone(_identity(), "12345"))
    assert e.value.code == "invalid_phone_number" and e.value.status == 422


def _identity():
    return SimpleNamespace(identity_id="ava-001", participant=SimpleNamespace(default_room="ava-001"))


@pytest.mark.parametrize("presence", ["OFFLINE", "STARTING", "ERROR"])
def test_dial_409_unless_ready_or_busy(db_file, presence):
    backend = RecordingBackend()
    c = app_client(db_file, presence, Service014Client("livekit_sip", backend))
    r = c.post("/identities/ava-001/phone/dial", json={"to": NUMBER}, headers=H["operator"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "identity_not_ready"
    assert r.json()["error"]["details"]["presence"] == presence
    assert_conforms("/identities/{identity_id}/phone/dial", "post", r)
    assert backend.calls == []


@pytest.mark.parametrize("presence", ["READY", "BUSY"])
def test_dial_202_when_ready_or_busy(db_file, presence):
    backend = RecordingBackend()
    c = app_client(db_file, presence, Service014Client("livekit_sip", backend))
    r = c.post("/identities/ava-001/phone/dial", json={"to": NUMBER}, headers=H["operator"])
    assert r.status_code == 202, r.text
    assert r.json() == {"call_id": "call-1", "room": "ava-001", "status": "answered"}
    assert_conforms("/identities/{identity_id}/phone/dial", "post", r)
    assert backend.calls == [("ava-001", NUMBER)]


def test_viewer_cannot_dial_and_unknown_identity_404(db_file):
    c = app_client(db_file, tel=Service014Client("livekit_sip", RecordingBackend()))
    assert c.post("/identities/ava-001/phone/dial", json={"to": NUMBER}, headers=H["viewer"]).status_code == 403
    r = c.post("/identities/zzz-001/phone/dial", json={"to": NUMBER}, headers=H["operator"])
    assert r.status_code == 404
    assert c.get("/identities/zzz-001/phone", headers=H["viewer"]).status_code == 404


def test_livekit_sip_info_reports_dispatch_rule_direct(db_file):
    c = app_client(db_file, tel=Service014Client.from_settings(sip_settings(db_file)))
    r = c.get("/identities/ava-001/phone", headers=H["viewer"])
    assert r.json() == {"mode": "livekit_sip", "default_room": "ava-001",
                        "inbound": {"kind": "SIPDispatchRuleDirect", "room_name": "ava-001"}}
    assert_conforms("/identities/{identity_id}/phone", "get", r)


def test_http_mode_without_api_key_is_503_not_silent_unauthenticated(db_file):
    s = Settings(SERVICE015_API_KEYS="adm:admin,ope:operator", SERVICE015_DB_PATH=str(db_file), _env_file=None,
                 SERVICE014_MODE="http", SERVICE014_URL="http://s014.test")
    c = app_client(db_file, tel=Service014Client.from_settings(s))
    r = c.post("/identities/ava-001/phone/dial", json={"to": NUMBER}, headers=H["operator"])
    assert r.status_code == 503 and r.json()["error"]["code"] == "telephony_not_configured"
    assert "SERVICE014_API_KEY" in r.json()["error"]["message"]
    with pytest.raises(ValueError):
        HttpBackend("http://s014.test", "")


def test_misconfigured_modes_are_503(db_file):
    for kw in (dict(SERVICE014_MODE="http"), dict(SERVICE014_MODE="livekit_sip")):
        s = Settings(SERVICE015_API_KEYS="adm:admin,ope:operator", SERVICE015_DB_PATH=str(db_file), _env_file=None, **kw)
        c = app_client(db_file, tel=Service014Client.from_settings(s))
        r = c.post("/identities/ava-001/phone/dial", json={"to": NUMBER}, headers=H["operator"])
        assert r.status_code == 503 and r.json()["error"]["code"] == "telephony_not_configured"


# ---- livekit_sip backend (mocked LiveKitAPI) ----

class FakeSip:
    def __init__(self, exc=None, sip_call_id="SCL_abc"):
        self.requests, self.exc, self.timeouts, self.sip_call_id = [], exc, [], sip_call_id

    async def create_sip_participant(self, request, *, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.exc:
            raise self.exc
        return SimpleNamespace(sip_call_id=self.sip_call_id, participant_id="PA_1")


class FakeLk:
    def __init__(self, sip):
        self.sip, self.closed = sip, False

    async def aclose(self):
        self.closed = True


@pytest.mark.anyio
async def test_livekit_sip_builds_exact_request():
    sip = FakeSip()
    lk = FakeLk(sip)
    out = await LiveKitSipBackend("ws://x", "k", "s", "ST_trunk", lambda: lk).dial("ava-001", NUMBER)
    assert out == {"call_id": "SCL_abc", "status": "answered"}
    req = sip.requests[0]
    assert isinstance(req, api.CreateSIPParticipantRequest)
    assert (req.sip_trunk_id, req.sip_call_to, req.room_name, req.wait_until_answered) == (
        "ST_trunk", NUMBER, "ava-001", True)
    assert re.fullmatch(r"phone-3210-[a-z0-9]{4}", req.participant_identity)
    assert req.ringing_timeout.seconds == 45  # bounded block: ringing timeout, then 502
    assert sip.timeouts == [60.0]
    assert lk.closed


@pytest.mark.anyio
async def test_livekit_sip_caller_identity_is_unique_per_call():
    ids = set()
    for _ in range(30):
        sip = FakeSip()
        await LiveKitSipBackend("ws://x", "k", "s", "ST", lambda sip=sip: FakeLk(sip)).dial("ava-001", NUMBER)
        ids.add(sip.requests[0].participant_identity)
    assert len(ids) > 20 and all(i.startswith("phone-3210-") for i in ids)


@pytest.mark.anyio
async def test_livekit_sip_falls_back_to_participant_id_when_no_sip_call_id():
    out = await LiveKitSipBackend("ws://x", "k", "s", "ST", lambda: FakeLk(FakeSip(sip_call_id=""))).dial("r", NUMBER)
    assert out == {"call_id": "PA_1", "status": "answered"}


@pytest.mark.anyio
async def test_livekit_sip_timeout_is_502_and_closes_client(caplog):
    lk = FakeLk(FakeSip(asyncio.TimeoutError()))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(TelephonyFailed) as e:
        await LiveKitSipBackend("ws://x", "k", "s", "ST", lambda: lk).dial("ava-001", NUMBER)
    assert e.value.status == 502 and e.value.code == "telephony_failed"
    assert lk.closed and NUMBER not in caplog.text


@pytest.mark.anyio
async def test_livekit_sip_failure_is_502_and_never_leaks_number(caplog):
    lk = FakeLk(FakeSip(ServerError("unavailable", "busy", status=503, metadata={"sip_status_code": "486"})))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(TelephonyFailed) as e:
        await LiveKitSipBackend("ws://x", "k", "s", "ST", lambda: lk).dial("ava-001", NUMBER)
    assert e.value.status == 502 and e.value.code == "telephony_failed"
    assert lk.closed
    assert NUMBER not in caplog.text and NUMBER not in str(e.value.details)


@pytest.mark.anyio
async def test_connect_phone_logs_only_masked_number(caplog):
    caplog.set_level(logging.DEBUG)
    out = await Service014Client("livekit_sip", RecordingBackend()).connect_phone(_identity(), NUMBER)
    assert out == {"call_id": "call-1", "room": "ava-001", "status": "answered"}
    assert NUMBER not in caplog.text and "3210" in caplog.text


# ---- http backend (httpx.MockTransport) ----

def http_backend(handler):
    return HttpBackend("http://s014.test/", "secret-key", transport=httpx.MockTransport(handler))


@pytest.mark.anyio
async def test_http_posts_room_and_to_with_bearer():
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), auth=request.headers["authorization"], body=json.loads(request.content))
        return httpx.Response(201, json={"call_id": "c-9", "status": "ringing"})

    out = await http_backend(handler).dial("ava-001", NUMBER)
    assert out == {"call_id": "c-9", "status": "ringing"}
    assert seen == {"url": "http://s014.test/calls", "auth": "Bearer secret-key", "body": {"room": "ava-001", "to": NUMBER}}


@pytest.mark.anyio
async def test_http_status_defaults_to_dialing():
    out = await http_backend(lambda r: httpx.Response(200, json={"call_id": "c"})).dial("r", NUMBER)
    assert out["status"] == "dialing"


@pytest.mark.parametrize("response,code", [
    (httpx.Response(500, text="x"), "telephony_failed"),
    (httpx.Response(200, text="not json"), "telephony_bad_response"),
    (httpx.Response(200, json={"status": "ok"}), "telephony_bad_response"),
])
@pytest.mark.anyio
async def test_http_failures_are_502(response, code):
    with pytest.raises(TelephonyFailed) as e:
        await http_backend(lambda r: response).dial("r", NUMBER)
    assert e.value.status == 502 and e.value.code == code


@pytest.mark.anyio
async def test_http_unreachable_is_502():
    def boom(request):
        raise httpx.ConnectError("down")

    with pytest.raises(TelephonyFailed) as e:
        await http_backend(boom).dial("r", NUMBER)
    assert e.value.code == "telephony_failed"


def test_http_end_to_end_through_api(db_file):
    backend = http_backend(lambda r: httpx.Response(200, json={"call_id": "c-1"}))
    c = app_client(db_file, tel=Service014Client("http", backend))
    r = c.post("/identities/ava-001/phone/dial", json={"to": NUMBER}, headers=H["operator"])
    assert r.status_code == 202 and r.json() == {"call_id": "c-1", "room": "ava-001", "status": "dialing"}
    assert c.get("/identities/ava-001/phone", headers=H["viewer"]).json()["inbound"] is None


def test_upstream_failure_502_conforms(db_file):
    backend = http_backend(lambda r: httpx.Response(503))
    c = app_client(db_file, tel=Service014Client("http", backend))
    r = c.post("/identities/ava-001/phone/dial", json={"to": NUMBER}, headers=H["operator"])
    assert r.status_code == 502 and r.json()["error"]["details"]["upstream_status"] == 503
    assert_conforms("/identities/{identity_id}/phone/dial", "post", r)
