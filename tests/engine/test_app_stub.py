"""App-level tests with the model and Voicebox stubbed (no torch, no network)."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine"))
import chatterbox_server as cs  # noqa: E402

PID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def client(monkeypatch):
    # No `with` block: the lifespan (real model load) must not run.
    monkeypatch.setattr(cs.STATE, "status", "ok")
    monkeypatch.setattr(cs.STATE, "conds", cs.OrderedDict())
    monkeypatch.setattr(cs.STATE, "requests", cs.OrderedDict())
    monkeypatch.setattr(cs.STATE, "prep_locks", {})
    return TestClient(cs.app, raise_server_exceptions=False)


def _fetch_raises(exc):
    async def fake(profile_id):
        raise exc
    return fake


def post(client, path, **body):
    return client.post(path, json={"profile_id": PID, "text": "Hello there, friend. This is a test.", **body})


@pytest.mark.parametrize("exc,status,code", [
    (cs.ReferenceTooShort(PID), 422, "reference_too_short"),
    (cs.CorruptReference(PID), 422, "corrupt_reference"),
    (cs.VoiceboxUnavailable("down"), 503, "voicebox_unavailable"),
    (cs.ProfileNotFound(PID), 404, "profile_not_found"),
])
@pytest.mark.parametrize("path", ["/synthesize/stream", "/preview", "/warm"])
def test_prepare_errors_are_coded(client, monkeypatch, exc, status, code, path):
    monkeypatch.setattr(cs, "fetch_reference", _fetch_raises(exc))
    r = client.post(path, json={"profile_id": PID, "text": "Hello there."}) if path != "/warm" else client.post(path, json={"profile_id": PID})
    assert r.status_code == status and r.json()["code"] == code
    assert cs.STATE.prep_locks == {}, "prep lock entries must not leak"


def test_unexpected_prepare_error_is_coded_500(client, monkeypatch):
    monkeypatch.setattr(cs, "fetch_reference", _fetch_raises(RuntimeError("boom")))
    r = post(client, "/preview")
    assert r.status_code == 500 and r.json()["code"] == "synthesis_failed"


@pytest.mark.parametrize("bad", ["../x", "a b", "", "x" * 65, "a/b"])
def test_profile_id_is_validated(client, bad):
    r = client.post("/warm", json={"profile_id": bad})
    assert r.status_code == 422 and r.json()["code"] == "invalid_request"


def test_not_ready_is_503(client, monkeypatch):
    monkeypatch.setattr(cs.STATE, "status", "loading")
    r = post(client, "/preview")
    assert r.status_code == 503 and r.json()["code"] == "engine_loading"


def _ready_profile(monkeypatch, fail_on_call=None):
    cs.STATE.conds[PID] = object()
    calls = {"n": 0}

    def fake_generate(profile_id, text):
        calls["n"] += 1
        if fail_on_call and calls["n"] == fail_on_call:
            raise RuntimeError("CUDA out of memory")
        return b"\x01\x00" * 2400

    monkeypatch.setattr(cs, "_generate_blocking", fake_generate)


def test_stream_ok_reports_complete(client, monkeypatch):
    _ready_profile(monkeypatch)
    r = post(client, "/synthesize/stream")
    assert r.status_code == 200 and r.headers["x-sample-rate"] == "24000"
    n = int(r.headers["x-chunk-count"])
    assert len(r.content) == n * 4800
    st = client.get(f"/requests/{r.headers['x-request-id']}").json()
    assert (st["state"], st["chunks_done"], st["chunks_total"], st["code"]) == ("complete", n, n, None)


def test_midstream_failure_is_signalled_and_coded(client, monkeypatch):
    _ready_profile(monkeypatch, fail_on_call=2)
    text = "This is the first sentence, which is fairly long indeed. And here is a second sentence to follow."
    try:
        r = client.post("/synthesize/stream", json={"profile_id": PID, "text": text})
        rid = r.headers["x-request-id"]
        total = int(r.headers["x-chunk-count"])
        assert len(r.content) < total * 4800  # truncated
    except Exception:  # the aborted response may also surface as a client-side error
        rid = next(iter(cs.STATE.requests))
    st = client.get(f"/requests/{rid}").json()
    assert st["state"] == "failed" and st["code"] == "synthesis_failed"
    assert st["chunks_done"] == 1 < st["chunks_total"]


def test_preview_midstream_failure_is_coded_json_not_bare_500(client, monkeypatch):
    _ready_profile(monkeypatch, fail_on_call=2)
    r = client.post("/preview", json={"profile_id": PID, "text": "This is the first sentence, which is fairly long indeed. And here is a second sentence to follow."})
    assert r.status_code == 500 and r.json()["code"] == "synthesis_failed"


def test_unknown_request_id_404(client):
    assert client.get("/requests/nope").json()["code"] == "request_not_found"
