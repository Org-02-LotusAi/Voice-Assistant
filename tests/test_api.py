import copy

from fastapi.testclient import TestClient

from tests.conftest import H, FakeSupervisor, make_app, make_settings, voiced_identity
from service015.api.app import create_app

AVA = {"name": "Ava", "company": "Safi", "role": "Account Specialist"}


def _instantiate(client, overrides=AVA, template="sales-representative", **extra):
    return client.post(f"/templates/{template}/instantiate", json={"overrides": overrides, **extra}, headers=H["operator"])


def test_health_no_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["dependencies"]["database"] == "ok"


def test_test1_three_templates_listed(client):
    r = client.get("/templates", headers=H["viewer"])
    assert r.status_code == 200
    assert {t["template_id"] for t in r.json()} == {"generic-assistant", "sales-representative", "customer-support"}


def test_test2_instantiate_ava_template_unchanged(client):
    before = client.get("/templates/sales-representative", headers=H["viewer"]).json()
    r = _instantiate(client)
    assert r.status_code == 201
    ava = r.json()
    assert ava["identity_id"] == "ava-001"
    assert ava["self"]["name"] == "Ava" and ava["template_version"] == 1
    assert ava["participant"] == {"participant_identity": "ava-001", "default_room": "ava-001"}
    assert client.get("/templates/sales-representative", headers=H["viewer"]).json() == before
    assert client.get("/templates/sales-representative/versions", headers=H["viewer"]).json() == [1]


def test_test3_customizations_stored_and_invalid_override_rejected(client):
    ov = {
        **AVA,
        "language": "en-GB",
        "persona.style": "warm and brief",
        "greeting": "Hi, I am {name} at {company}.",
        "conversation.barge_in": False,
        "conversation.speaking_style": "calm",
    }
    ava = _instantiate(client, ov).json()
    got = client.get("/identities/ava-001", headers=H["viewer"]).json()
    assert got == ava
    assert got["overrides"] == ov
    assert got["self"]["language"] == "en-GB"
    assert got["conversation"]["greeting"] == "Hi, I am Ava at Safi."
    assert got["conversation"]["barge_in"] is False
    bad = _instantiate(client, {**AVA, "template_id": "x"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "override_not_allowed"
    assert bad.json()["error"]["details"]["field"] == "template_id"
    missing = _instantiate(client, {"name": "Zed"})
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "missing_required_field"


def test_patch_self_context_status_and_roster(client):
    _instantiate(client)
    p = client.patch("/identities/ava-001", json={"overrides": {"role": "Senior Specialist"}}, headers=H["operator"])
    assert p.status_code == 200 and p.json()["self"]["role"] == "Senior Specialist"
    assert p.json()["identity_id"] == "ava-001"
    sc = client.get("/identities/ava-001/self-context", headers=H["viewer"]).json()
    assert "name: Ava" in sc["self_context"] and "My name is Ava." in sc["instructions"]
    assert sc["resolved_hash"] == p.json()["resolved_hash"]
    st = client.get("/identities/ava-001/status", headers=H["viewer"]).json()
    assert st["presence"] == "OFFLINE" and st["capabilities"]["barge_in"] is True
    assert "pid" not in st
    roster = client.get("/meeting/participants", headers=H["viewer"]).json()
    assert roster == [st]
    summaries = client.get("/identities", headers=H["viewer"]).json()
    assert summaries[0]["identity_id"] == "ava-001" and summaries[0]["name"] == "Ava"


def test_barge_in_mirrored_in_status(client):
    _instantiate(client, {**AVA, "conversation.barge_in": False})
    assert client.get("/identities/ava-001/status", headers=H["viewer"]).json()["capabilities"]["barge_in"] is False


def test_launch_stop_null_supervisor_503(client):
    voiced_identity(client)
    for op in ("launch", "stop"):
        r = client.post(f"/identities/ava-001/{op}", headers=H["operator"])
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "dependency_unavailable"
        assert r.json()["error"]["message"] == "runtime not configured"
    assert client.post("/identities/nope-001/launch", headers=H["operator"]).status_code == 404


def test_injected_supervisor_used(db_file, voice_stack):
    with TestClient(make_app(db_file, voice_stack, FakeSupervisor())) as c:
        voiced_identity(c)
        assert c.post("/identities/ava-001/launch", headers=H["operator"]).json()["presence"] == "STARTING"
        assert c.get("/health").json()["dependencies"]["runtime"] == "ok"


def test_not_found_and_validation_error_shape(client):
    r = client.get("/identities/missing-001", headers=H["viewer"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "identity_not_found"
    r = client.get("/templates/nope", headers=H["viewer"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "template_not_found"
    r = client.post("/templates/sales-representative/instantiate", json={"bogus": 1}, headers=H["operator"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    r = client.get("/templates/sales-representative?version=0", headers=H["viewer"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert client.get("/no-such-route", headers=H["viewer"]).json()["error"]["code"] == "not_found"


def test_create_template_new_version_admin_only(client):
    body = copy.deepcopy(client.get("/templates/generic-assistant", headers=H["viewer"]).json())
    for k in ("version", "created_at"):
        body.pop(k)
    body["description"] = "second edition"
    assert client.post("/templates", json=body, headers=H["operator"]).status_code == 403
    r = client.post("/templates", json=body, headers=H["admin"])
    assert r.status_code == 201 and r.json()["version"] == 2
    assert client.get("/templates/generic-assistant/versions", headers=H["viewer"]).json() == [1, 2]
    assert client.get("/templates/generic-assistant?version=1", headers=H["viewer"]).json()["description"] != "second edition"
    body["allowed_overrides"] = ["bogus"]
    assert client.post("/templates", json=body, headers=H["admin"]).json()["error"]["code"] == "invalid_request"


def test_rbac(client):
    assert client.get("/templates").status_code == 401
    r = client.get("/templates", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    assert client.get("/templates", headers={"Authorization": "Basic adm"}).status_code == 401
    r = client.post("/templates/sales-representative/instantiate", json={"overrides": AVA}, headers=H["viewer"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert client.get("/openapi.yaml").status_code == 200  # public


def test_restart_persistence(db_file):
    with TestClient(create_app(make_settings(db_file))) as c1:
        first = _instantiate(c1).json()
    with TestClient(create_app(make_settings(db_file))) as c2:
        again = c2.get("/identities/ava-001", headers=H["viewer"]).json()
        assert len(c2.get("/templates", headers=H["viewer"]).json()) == 3
    assert again["resolved_hash"] == first["resolved_hash"]
    assert again["identity_id"] == "ava-001"
    assert again["participant"]["default_room"] == first["participant"]["default_room"] == "ava-001"
    assert again == first


def test_health_degraded_when_db_unreachable(tmp_path, caplog):
    # db_path is a directory: sqlite cannot open it. No lifespan, so startup does not fail first.
    c = TestClient(create_app(make_settings(tmp_path)))
    with caplog.at_level("ERROR", logger="service015.api"):
        r = c.get("/health")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded" and r.json()["dependencies"]["database"] == "error"
    assert "database unreachable" in caplog.text


def test_settings_repr_hides_api_keys(tmp_path):
    assert "api_keys" not in repr(make_settings(tmp_path))
