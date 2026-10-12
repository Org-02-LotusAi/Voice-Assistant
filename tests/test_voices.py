import asyncio
import io
import logging
import wave

import httpx
import pytest

from tests.conftest import H, make_app, voiced_identity
from tests.test_openapi_contract import assert_conforms
from tests.voice_fakes import make_wav
from service015.errors import DependencyUnavailable, NotFound, ValidationFailed
from service015.voice.engine import VoiceEngineClient, VoiceEngineFailed
from service015.voice.voicebox import VoiceboxClient

AUTH = {"consent_statement": "Synthetic voice, authorized by construction.", "subject_name": "Zira (synthetic)",
        "authorized_by": "ops@example.com", "source": "test"}
OV = {"name": "Ava", "company": "Safi"}


def authorize(client, profile_id="vp-ava"):
    r = client.post(f"/voices/{profile_id}/authorizations", json=AUTH, headers=H["admin"])
    assert r.status_code == 201, r.text
    return r.json()


def instantiate(client, **extra):
    return client.post("/templates/sales-representative/instantiate",
                       json={"overrides": {**OV, **extra}}, headers=H["operator"])


def error_code(r):
    return r.json()["error"]["code"]


# ---------- GET /voices ----------

def test_list_voices_returns_only_cloned_profiles_with_authorized_flag(client):
    r = client.get("/voices", headers=H["viewer"])
    assert r.status_code == 200
    assert r.json() == [{"voice_profile_id": "vp-ava", "name": "Ava Warm", "language": "en",
                         "engine": "chatterbox-turbo", "authorized": False, "shared": False, "owned_by_me": False}]
    assert_conforms("/voices", "get", r)
    authorize(client)
    listed = client.get("/voices", headers=H["viewer"]).json()[0]
    assert listed["authorized"] is True and listed["shared"] is True and listed["owned_by_me"] is False  # unowned = shared


def test_list_voices_voicebox_down_is_503(client, voice_stack):
    voice_stack.voicebox.down = True
    r = client.get("/voices", headers=H["viewer"])
    assert r.status_code == 503 and error_code(r) == "voicebox_unavailable"
    assert_conforms("/voices", "get", r)


# ---------- POST /voices ----------

def create_form(**over):
    data = {"name": "Ben Calm", "language": "en", "description": "calm", "reference_text": "this is the transcript",
            "consent_statement": "I consent.", "subject_name": "Ben", "authorized_by": "Ops"}
    data.update(over)
    return data


def post_voice(client, data=None, sample=None, role="admin"):
    sample = make_wav(6.0) if sample is None else sample
    return client.post("/voices", data=data or create_form(), files={"file": ("ref.wav", sample, "audio/wav")},
                       headers=H[role])


def test_create_voice_makes_profile_and_authorization(client, voice_stack):
    r = post_voice(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Ben Calm" and body["authorized"] is True and body["engine"] == "chatterbox-turbo"
    assert_conforms("/voices", "post", r)
    assert voice_stack.voicebox.samples[body["voice_profile_id"]], "sample was uploaded to Voicebox"
    listed = {v["voice_profile_id"]: v for v in client.get("/voices", headers=H["viewer"]).json()}
    assert listed[body["voice_profile_id"]]["authorized"] is True
    # and the new voice can be attached to an identity
    assert instantiate(client, **{"voice.profile_id": body["voice_profile_id"]}).status_code == 201


def test_create_voice_voicebox_down_records_nothing(client, voice_stack):
    voice_stack.voicebox.down = True
    r = post_voice(client)
    assert r.status_code == 503 and error_code(r) == "voicebox_unavailable"
    voice_stack.voicebox.down = False
    assert all(not v["authorized"] for v in client.get("/voices", headers=H["viewer"]).json())


def test_create_voice_sample_rejected_removes_profile_and_records_nothing(client, voice_stack):
    voice_stack.voicebox.fail_sample_upload = True
    before = set(voice_stack.voicebox.profiles)
    r = post_voice(client)
    assert r.status_code == 502 and error_code(r) == "voicebox_rejected"
    assert_conforms("/voices", "post", r)
    assert set(voice_stack.voicebox.profiles) == before, "half-created profile must be deleted"
    voice_stack.voicebox.fail_sample_upload = False
    assert all(not v["authorized"] for v in client.get("/voices", headers=H["viewer"]).json())


def test_create_voice_rejects_empty_sample_and_missing_consent(client, voice_stack):
    r = post_voice(client, sample=b"")
    assert r.status_code == 422 and error_code(r) == "empty_sample"
    bad = create_form()
    del bad["consent_statement"]
    r = post_voice(client, data=bad)
    assert r.status_code == 422 and error_code(r) == "invalid_request"
    r = post_voice(client, data=create_form(subject_name="   "))
    assert r.status_code == 422 and error_code(r) == "invalid_request"
    assert not any(m == "POST" and p == "/profiles" for m, p in voice_stack.voicebox.requests), "nothing reached Voicebox"


def test_create_voice_requires_admin(client):
    assert post_voice(client, role="operator").status_code == 403


def test_raw_sample_audio_is_never_returned_or_logged(client, caplog):
    marker = b"RAWSAMPLEMARKER" * 10
    with caplog.at_level(logging.DEBUG):
        r = post_voice(client, sample=make_wav(6.0) + marker)
    assert r.status_code == 201
    assert b"RAWSAMPLEMARKER" not in r.content
    assert "RAWSAMPLEMARKER" not in caplog.text


# ---------- POST /voices/{id}/authorizations ----------

def test_authorize_existing_voicebox_profile(client):
    row = authorize(client)
    assert row["voice_profile_id"] == "vp-ava" and row["authorized_by"] == "ops@example.com" and row["id"] >= 1
    r = client.post("/voices/vp-ava/authorizations", json=AUTH, headers=H["admin"])
    assert_conforms("/voices/{voice_profile_id}/authorizations", "post", r)


def test_authorize_unknown_or_non_cloned_profile_fails(client):
    r = client.post("/voices/nope/authorizations", json=AUTH, headers=H["admin"])
    assert r.status_code == 404 and error_code(r) == "voice_profile_not_found"
    r = client.post("/voices/vp-preset/authorizations", json=AUTH, headers=H["admin"])
    assert r.status_code == 422 and error_code(r) == "voice_profile_not_cloned"
    r = client.post("/voices/vp-ava/authorizations", json={**AUTH, "consent_statement": ""}, headers=H["admin"])
    assert r.status_code == 422 and error_code(r) == "invalid_request"


# ---------- POST /voices/{id}/preview ----------

def wav_info(content):
    with wave.open(io.BytesIO(content)) as w:
        return w.getnchannels(), w.getframerate()


def test_preview_returns_wav_with_default_and_custom_text(client, voice_stack):
    authorize(client)
    r = client.post("/voices/vp-ava/preview", headers=H["operator"])
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert wav_info(r.content) == (1, 24000)
    r = client.post("/voices/vp-ava/preview", json={"text": "Good morning."}, headers=H["operator"])
    assert r.status_code == 200
    assert voice_stack.engine.previews == [("vp-ava", "Hello, this is a preview of my voice."),
                                           ("vp-ava", "Good morning.")]
    assert_conforms("/voices/{voice_profile_id}/preview", "post", r)


def test_preview_requires_existing_authorized_voice(client, voice_stack):
    r = client.post("/voices/vp-ava/preview", headers=H["operator"])
    assert r.status_code == 422 and error_code(r) == "voice_not_authorized"
    r = client.post("/voices/nope/preview", headers=H["operator"])
    assert r.status_code == 404 and error_code(r) == "voice_profile_not_found"
    assert voice_stack.engine.previews == []
    assert client.post("/voices/vp-ava/preview", headers=H["viewer"]).status_code == 403
    assert client.post("/voices/vp-ava/preview", json={"text": "x" * 501}, headers=H["operator"]).status_code == 422


def test_preview_engine_failures_map_to_errors(client, voice_stack):
    authorize(client)
    voice_stack.engine.down = True
    r = client.post("/voices/vp-ava/preview", headers=H["operator"])
    assert r.status_code == 503 and error_code(r) == "voice_engine_unavailable"
    assert_conforms("/voices/{voice_profile_id}/preview", "post", r)
    voice_stack.engine.down = False
    voice_stack.engine.fail_with = (422, {"code": "reference_too_short", "detail": "sample must exceed 5 s"})
    r = client.post("/voices/vp-ava/preview", headers=H["operator"])
    assert r.status_code == 422 and error_code(r) == "reference_too_short"
    voice_stack.engine.fail_with = (503, {"code": "voicebox_unavailable", "detail": "x"})
    r = client.post("/voices/vp-ava/preview", headers=H["operator"])
    assert r.status_code == 503 and error_code(r) == "voicebox_unavailable"
    voice_stack.engine.fail_with = (503, {"code": "engine_loading", "detail": "x"})
    r = client.post("/voices/vp-ava/preview", headers=H["operator"])
    assert r.status_code == 503 and error_code(r) == "voice_engine_unavailable"
    voice_stack.engine.fail_with = (500, {"code": "boom", "detail": "x"})
    r = client.post("/voices/vp-ava/preview", headers=H["operator"])
    assert r.status_code == 502 and error_code(r) == "voice_engine_error"
    assert_conforms("/voices/{voice_profile_id}/preview", "post", r)


def test_preview_voicebox_down_is_503(client, voice_stack):
    authorize(client)
    voice_stack.voicebox.down = True
    r = client.post("/voices/vp-ava/preview", headers=H["operator"])
    assert r.status_code == 503 and error_code(r) == "voicebox_unavailable"


# ---------- instantiate / patch enforcement ----------

def identity_count(client):
    return len(client.get("/identities", headers=H["viewer"]).json())


def test_instantiate_rejects_unknown_and_unauthorized_voice(client):
    r = instantiate(client, **{"voice.profile_id": "nope"})
    assert r.status_code == 422 and error_code(r) == "voice_profile_not_found"
    assert_conforms("/templates/{template_id}/instantiate", "post", r)
    r = instantiate(client, **{"voice.profile_id": "vp-ava"})
    assert r.status_code == 422 and error_code(r) == "voice_not_authorized"
    assert r.json()["error"]["details"]["voice_profile_id"] == "vp-ava"
    assert identity_count(client) == 0


def test_instantiate_accepts_authorized_voice(client):
    authorize(client)
    r = instantiate(client, **{"voice.profile_id": "vp-ava"})
    assert r.status_code == 201 and r.json()["voice"]["profile_id"] == "vp-ava"
    assert client.get("/identities", headers=H["viewer"]).json()[0]["voice_profile_id"] == "vp-ava"


def test_instantiate_voicebox_down_is_503_and_persists_nothing(client, voice_stack):
    authorize(client)
    voice_stack.voicebox.down = True
    r = instantiate(client, **{"voice.profile_id": "vp-ava"})
    assert r.status_code == 503 and error_code(r) == "voicebox_unavailable"
    assert_conforms("/templates/{template_id}/instantiate", "post", r)
    assert identity_count(client) == 0
    # no voice requested -> Voicebox is not needed at all
    assert instantiate(client).status_code == 201


def test_instantiate_unknown_template_is_404_without_calling_voicebox(client, voice_stack):
    r = client.post("/templates/nope/instantiate", json={"overrides": {"voice.profile_id": "vp-ava"}}, headers=H["operator"])
    assert r.status_code == 404
    assert voice_stack.voicebox.requests == []


def test_patch_enforces_voice_rules_and_leaves_identity_unchanged(client, voice_stack):
    ident = instantiate(client).json()["identity_id"]
    path = f"/identities/{ident}"
    for value, code in (("nope", "voice_profile_not_found"), ("vp-ava", "voice_not_authorized")):
        r = client.patch(path, json={"overrides": {"voice.profile_id": value}}, headers=H["operator"])
        assert r.status_code == 422 and error_code(r) == code
        assert_conforms("/identities/{identity_id}", "patch", r)
    assert client.get(path, headers=H["viewer"]).json()["voice"]["profile_id"] is None

    authorize(client)
    voice_stack.voicebox.down = True
    r = client.patch(path, json={"overrides": {"voice.profile_id": "vp-ava"}}, headers=H["operator"])
    assert r.status_code == 503 and error_code(r) == "voicebox_unavailable"
    assert client.get(path, headers=H["viewer"]).json()["voice"]["profile_id"] is None
    voice_stack.voicebox.down = False

    r = client.patch(path, json={"overrides": {"voice.profile_id": "vp-ava"}}, headers=H["operator"])
    assert r.status_code == 200 and r.json()["voice"]["profile_id"] == "vp-ava"

    # unrelated patch and clearing the voice do not need Voicebox
    voice_stack.voicebox.down = True
    assert client.patch(path, json={"overrides": {"role": "Lead"}}, headers=H["operator"]).status_code == 200
    r = client.patch(path, json={"overrides": {"voice.profile_id": None}}, headers=H["operator"])
    assert r.status_code == 200 and r.json()["voice"]["profile_id"] is None


def test_patch_unknown_identity_is_404(client):
    r = client.patch("/identities/zzz-001", json={"overrides": {"voice.profile_id": "vp-ava"}}, headers=H["operator"])
    assert r.status_code == 404


# ---------- /health ----------

def test_health_reports_voice_dependencies(client, voice_stack):
    r = client.get("/health")
    assert r.status_code == 200
    deps = r.json()["dependencies"]
    assert deps["voicebox"] == "ok" and deps["voice_engine"] == "ok"
    assert_conforms("/health", "get", r)
    voice_stack.engine.status = "loading"
    assert client.get("/health").json()["dependencies"]["voice_engine"] == "loading"


def test_health_never_fails_when_voice_services_are_down(client, voice_stack):
    voice_stack.voicebox.down = True
    voice_stack.engine.down = True
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert r.json()["dependencies"]["voicebox"] == "unavailable"
    assert r.json()["dependencies"]["voice_engine"] == "unavailable"
    assert_conforms("/health", "get", r)


# ---------- clients ----------

def run(coro):
    return asyncio.run(coro)


def test_voicebox_client_get_profile_unknown_is_none_and_never_traverses_paths(voice_stack):
    vb = voice_stack.voicebox
    assert run(vb.client.get_profile("vp-ava"))["name"] == "Ava Warm"
    assert run(vb.client.get_profile("missing")) is None
    n = len(vb.requests)
    assert run(vb.client.get_profile("../profiles")) is None and run(vb.client.get_profile("")) is None
    assert len(vb.requests) == n, "ids that could alter the path are rejected before any request"


def test_voicebox_client_5xx_and_transport_errors_are_unavailable():
    def handler(request):
        return httpx.Response(502, text="bad gateway")

    c = VoiceboxClient("http://vb.test", 1.0, httpx.MockTransport(handler))
    with pytest.raises(DependencyUnavailable) as ei:
        run(c.list_profiles())
    assert ei.value.code == "voicebox_unavailable" and ei.value.status == 503
    assert run(c.health()) == "unavailable"
    assert run(VoiceboxClient("http://127.0.0.1:9", 1.0).health()) == "unavailable"


def test_engine_client_preview_warm_stream(voice_stack):
    ec = voice_stack.engine.client
    assert wav_info(run(ec.preview("vp-ava", "Hi there."))) == (1, 24000)
    assert run(ec.warm("vp-ava")) == {"warm": True}

    async def collect():
        return [c async for c in ec.stream("vp-ava", "Hi there.")]

    chunks = run(collect())
    assert b"".join(chunks) == b"\x01\x00" * 100 * 3


def test_engine_client_error_mapping(voice_stack):
    ec = voice_stack.engine.client
    with pytest.raises(NotFound) as ei:
        run(ec.preview("missing", "Hi."))
    assert ei.value.code == "voice_profile_not_found"

    async def collect():
        return [c async for c in ec.stream("missing", "Hi.")]

    with pytest.raises(NotFound):
        run(collect())
    voice_stack.engine.fail_with = (422, {"code": "reference_too_short", "detail": "d"})
    with pytest.raises(ValidationFailed) as ei2:
        run(ec.warm("vp-ava"))
    assert ei2.value.code == "reference_too_short"
    voice_stack.engine.fail_with = (500, {"code": "x", "detail": "d"})
    with pytest.raises(VoiceEngineFailed):
        run(ec.preview("vp-ava", "Hi."))


def test_engine_client_timeout_is_unavailable():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    c = VoiceEngineClient("http://engine.test", 0.1, httpx.MockTransport(handler))
    with pytest.raises(DependencyUnavailable) as ei:
        run(c.preview("vp-ava", "Hi."))
    assert ei.value.code == "voice_engine_unavailable"
    assert run(c.health()) == "unavailable"


def test_settings_expose_configurable_engine_timeout(monkeypatch, db_file):
    from service015.config import Settings

    assert Settings(_env_file=None).voice_engine_timeout_s == 120.0
    monkeypatch.setenv("VOICE_ENGINE_TIMEOUT_S", "7.5")
    assert Settings(_env_file=None).voice_engine_timeout_s == 7.5


# ---------- scripts/authorize_seed_voices.py ----------

def _load_seed_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "authorize_seed_voices.py"
    spec = importlib.util.spec_from_file_location("authorize_seed_voices", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_seed_script_records_provenance_rows_idempotently(voice_stack, db_file, monkeypatch):
    import sqlite3

    mod = _load_seed_script()
    vb = voice_stack.voicebox
    vb.profiles["vp-james"] = {"id": "vp-james", "name": "James Calm", "language": "en", "voice_type": "cloned"}
    monkeypatch.setattr(mod, "VoiceboxClient", lambda url, timeout: vb.client)
    assert run(mod.run(str(db_file), "http://x")) == 0
    assert run(mod.run(str(db_file), "http://x")) == 0
    rows = sqlite3.connect(db_file).execute(
        "SELECT voice_profile_id, subject_name, consent_statement, source FROM voice_authorizations ORDER BY id"
    ).fetchall()
    assert [r[0] for r in rows] == ["vp-ava", "vp-james"], "second run must not add rows"
    assert "Zira" in rows[0][1] and "David" in rows[1][1]
    assert "Authorized by construction" in rows[0][2] and rows[0][3].startswith("seed_provenance:")


def test_seed_script_fails_when_profile_missing(voice_stack, db_file, monkeypatch, capsys):
    mod = _load_seed_script()
    monkeypatch.setattr(mod, "VoiceboxClient", lambda url, timeout: voice_stack.voicebox.client)  # no James Calm
    assert run(mod.run(str(db_file), "http://x")) == 1
    assert "James Calm" in capsys.readouterr().err


# ---------- stream truncation protocol (engine/README.md) ----------

def collect_stream(ec):
    async def go():
        got = []
        try:
            async for c in ec.stream("vp-ava", "Hi there."):
                got.append(c)
        except Exception as exc:  # noqa: BLE001 - the test inspects what was raised
            return got, exc
        return got, None

    return run(go())


def test_stream_complete_has_no_error(voice_stack):
    got, err = collect_stream(voice_stack.engine.client)
    assert err is None and len(got) == 3


@pytest.mark.parametrize("mode,code,state", [
    ("failed", "synthesis_failed", "failed"),
    ("truncated", "tts_failed", "complete"),   # status says complete but chunks_done < chunks_total
    ("cancelled", "tts_failed", "cancelled"),
    ("break", "synthesis_failed", "failed"),   # connection dropped mid-body; status carries the coded reason
    ("no_id", "tts_failed", None),
    ("status_404", "tts_failed", None),
])
def test_stream_abnormal_end_raises_coded_error(voice_stack, mode, code, state):
    voice_stack.engine.stream_mode = mode
    got, err = collect_stream(voice_stack.engine.client)
    assert err is not None, "an incomplete stream must never end silently"
    assert getattr(err, "code", None) == code, err
    if state:
        assert err.details["state"] == state
    if mode not in ("no_id", "status_404"):  # those deliver all bytes but cannot be verified
        assert len(got) < 3


def test_stream_failed_with_engine_codes_maps_domain_errors(voice_stack):
    voice_stack.engine.stream_mode = "failed"
    for engine_code, exc_type, code in (("voicebox_unavailable", DependencyUnavailable, "voicebox_unavailable"),
                                        ("reference_too_short", ValidationFailed, "reference_too_short"),
                                        ("profile_not_found", NotFound, "voice_profile_not_found")):
        voice_stack.engine.stream_code = engine_code
        _, err = collect_stream(voice_stack.engine.client)
        assert isinstance(err, exc_type) and err.code == code


# ---------- launch preflight ----------

def test_launch_requires_a_voice(db_file, voice_stack):
    from fastapi.testclient import TestClient
    from tests.conftest import FakeSupervisor

    with TestClient(make_app(db_file, voice_stack, FakeSupervisor())) as c:
        assert instantiate(c).status_code == 201  # draft identity without a voice is allowed
        before = c.get("/identities/ava-001", headers=H["viewer"]).json()
        r = c.post("/identities/ava-001/launch", headers=H["operator"])
        assert r.status_code == 422 and error_code(r) == "voice_required"
        assert_conforms("/identities/{identity_id}/launch", "post", r)
        assert c.get("/identities/ava-001", headers=H["viewer"]).json() == before


def test_launch_rechecks_voice_existence_authorization_and_voicebox(db_file, voice_stack):
    from fastapi.testclient import TestClient
    from tests.conftest import FakeSupervisor

    class Spy(FakeSupervisor):
        launched = 0

        async def launch(self, identity_id):
            Spy.launched += 1
            return await super().launch(identity_id)

    with TestClient(make_app(db_file, voice_stack, Spy())) as c:
        assert voiced_identity(c).status_code == 201
        before = c.get("/identities/ava-001", headers=H["viewer"]).json()
        voice_stack.voicebox.down = True
        r = c.post("/identities/ava-001/launch", headers=H["operator"])
        assert r.status_code == 503 and error_code(r) == "voicebox_unavailable"
        voice_stack.voicebox.down = False
        saved = voice_stack.voicebox.profiles.pop("vp-ava")  # profile deleted in Voicebox after attach
        r = c.post("/identities/ava-001/launch", headers=H["operator"])
        assert r.status_code == 422 and error_code(r) == "voice_profile_not_found"
        voice_stack.voicebox.profiles["vp-ava"] = saved
        assert Spy.launched == 0, "supervisor must not be reached while the preflight fails"
        assert c.post("/identities/ava-001/launch", headers=H["operator"]).status_code == 200
        assert Spy.launched == 1
        assert c.get("/identities/ava-001", headers=H["viewer"]).json() == before


def test_launch_unauthorized_voice_is_422(db_file, voice_stack):
    import sqlite3

    from fastapi.testclient import TestClient
    from tests.conftest import FakeSupervisor

    with TestClient(make_app(db_file, voice_stack, FakeSupervisor())) as c:
        assert voiced_identity(c).status_code == 201
        before = c.get("/identities/ava-001", headers=H["viewer"]).json()
        db = sqlite3.connect(db_file)
        db.execute("DELETE FROM voice_authorizations")  # simulates a missing authorization row
        db.commit()
        db.close()
        r = c.post("/identities/ava-001/launch", headers=H["operator"])
        assert r.status_code == 422 and error_code(r) == "voice_not_authorized"
        assert c.get("/identities/ava-001", headers=H["viewer"]).json() == before


# ---------- upload hardening ----------

def raw_post(client, sample, filename="ref.wav", ctype="audio/wav", role="admin"):
    return client.post("/voices", data=create_form(), files={"file": (filename, sample, ctype)}, headers=H[role])


def test_oversize_content_length_is_413_before_anything_else(client, voice_stack):
    r = client.post("/voices", content=b"x", headers={**H["admin"], "content-type": "multipart/form-data; boundary=b",
                                                      "content-length": str(26 * 1024 * 1024 + 512 * 1024)})
    assert r.status_code == 413 and error_code(r) == "sample_too_large"
    assert_conforms("/voices", "post", r)
    assert voice_stack.voicebox.requests == []


def test_oversize_file_is_413(client, monkeypatch):
    from service015.api.routers import voices

    monkeypatch.setattr(voices, "MAX_SAMPLE_BYTES", 1000)
    r = raw_post(client, make_wav(1.0))
    assert r.status_code == 413 and error_code(r) == "sample_too_large"


def test_missing_content_length_is_413(client):
    def body():
        yield b"--b--\r\n"

    r = client.post("/voices", content=body(), headers={**H["admin"], "content-type": "multipart/form-data; boundary=b"})
    assert r.status_code == 413 and error_code(r) == "sample_too_large"


def test_bad_media_type_or_magic_bytes_are_415(client, voice_stack):
    r = raw_post(client, make_wav(1.0), ctype="text/plain")
    assert r.status_code == 415 and error_code(r) == "unsupported_media_type"
    assert_conforms("/voices", "post", r)
    r = raw_post(client, b"MZ\x90\x00 this is an executable, not audio", ctype="audio/wav")
    assert r.status_code == 415
    assert not any(m == "POST" and p == "/profiles" for m, p in voice_stack.voicebox.requests)


@pytest.mark.parametrize("head,ext", [(b"RIFF\x00\x00\x00\x00WAVEfmt ", "wav"), (b"ID3\x04\x00", "mp3"), (b"\xff\xfb\x90\x00", "mp3"),
                                      (b"\x00\x00\x00\x18ftypM4A ", "m4a"), (b"OggS\x00\x02", "ogg"), (b"fLaC\x00", "flac"),
                                      (bytes.fromhex("1a45dfa3") + b"\x01", "webm")])
def test_sniff_audio_accepts_supported_containers(head, ext):
    from service015.api.routers.voices import sniff_audio

    assert sniff_audio(head.ljust(16, b"\x00")) == ext


def test_filename_is_sanitized_to_a_safe_basename():
    from service015.api.routers.voices import safe_filename

    assert safe_filename("../../etc/pass wd.wav", "wav") == "pass_wd.wav"
    assert safe_filename("C:\\Users\\x\\voice;rm -rf.wav", "wav") == "voice_rm_-rf.wav"
    assert safe_filename("...", "wav") == "reference.wav" and safe_filename(None, "mp3") == "reference.mp3"
    assert len(safe_filename("a" * 300 + ".wav", "wav")) <= 64


def test_auth_insert_failure_deletes_the_voicebox_profile(client, voice_stack, monkeypatch):
    from service015.errors import DomainError
    from service015.voice import authz

    def boom(*a, **k):
        raise DomainError("db_down", "cannot write", status=500)

    monkeypatch.setattr(authz, "record_authorization", boom)
    r = post_voice(client)
    assert r.status_code == 500
    deletes = [p for m, p in voice_stack.voicebox.requests if m == "DELETE"]
    assert len(deletes) == 1 and deletes[0].startswith("/profiles/vp-new-")
    assert not any(k.startswith("vp-new-") for k in voice_stack.voicebox.profiles)


def test_cancellation_between_create_and_insert_still_compensates(client, voice_stack, monkeypatch):
    from service015.voice import authz

    def cancelled(*a, **k):
        raise asyncio.CancelledError()

    monkeypatch.setattr(authz, "record_authorization", cancelled)
    try:
        post_voice(client)
    except BaseException:  # noqa: BLE001 - the cancel may surface through the test transport
        pass
    assert any(m == "DELETE" for m, _ in voice_stack.voicebox.requests)


def test_get_profile_id_guard_is_strict(voice_stack):
    vb = voice_stack.voicebox
    n = len(vb.requests)
    for bad in ("a b", "a?b", "a#b", "a%2fb", "..%2f", "a.b", "x" * 65, "ab\n"):
        assert run(vb.client.get_profile(bad)) is None
    assert len(vb.requests) == n
