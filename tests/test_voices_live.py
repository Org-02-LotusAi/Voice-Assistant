"""Live checks against the real Voicebox (:17493) and engine sidecar (:17494); skipped when unreachable."""
import io
import os
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import H, make_settings
from service015.api.app import create_app
from service015.voice.engine import VoiceEngineClient
from service015.voice.voicebox import VoiceboxClient

VOICEBOX_URL = os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493")
ENGINE_URL = os.environ.get("VOICE_ENGINE_URL", "http://127.0.0.1:17494")
SEEDED = {"Ava Warm": "6326829e-38ae-475f-aedb-5c293aa43e51", "James Calm": "1ae6b96a-8410-4455-af19-7c58bcdfbd94"}


def _up(url, ok_status=("ok",)):
    try:
        r = httpx.get(url + "/health", timeout=3)
        return r.status_code == 200 and (r.json().get("status") in ok_status or "status" not in r.json())
    except (httpx.HTTPError, ValueError):
        return False


needs_voicebox = pytest.mark.skipif(not _up(VOICEBOX_URL, ("healthy", "ok")), reason="Voicebox not reachable")
needs_engine = pytest.mark.skipif(not (_up(VOICEBOX_URL, ("healthy", "ok")) and _up(ENGINE_URL)), reason="engine not reachable/ready")


@pytest.fixture
def live_client(db_file):
    app = create_app(
        make_settings(db_file),
        voicebox=VoiceboxClient(VOICEBOX_URL, 30.0),
        voice_engine=VoiceEngineClient(ENGINE_URL, float(os.environ.get("VOICE_ENGINE_TIMEOUT_S", "180"))),
    )
    with TestClient(app) as c:
        yield c


@needs_voicebox
def test_live_list_voices_shows_seeded_profiles(live_client):
    r = live_client.get("/voices", headers=H["viewer"])
    assert r.status_code == 200, r.text
    by_name = {v["name"]: v for v in r.json()}
    for name, pid in SEEDED.items():
        assert by_name[name]["voice_profile_id"] == pid
        assert by_name[name]["engine"] == "chatterbox-turbo" and by_name[name]["authorized"] is False  # fresh temp DB


@needs_voicebox
def test_live_health_reports_voicebox(live_client):
    deps = live_client.get("/health").json()["dependencies"]
    assert deps["voicebox"] == "ok" and deps["voice_engine"] in ("ok", "loading", "unavailable", "error")


@needs_voicebox
def test_live_instantiate_enforces_authorization(live_client):
    pid = SEEDED["Ava Warm"]
    body = {"overrides": {"name": "Ava", "company": "Safi", "voice.profile_id": pid}}
    r = live_client.post("/templates/sales-representative/instantiate", json=body, headers=H["operator"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "voice_not_authorized"
    auth = {"consent_statement": "Synthetic seed voice, authorized by construction.", "subject_name": "Zira (synthetic)",
            "authorized_by": "test", "source": "live-test"}
    assert live_client.post(f"/voices/{pid}/authorizations", json=auth, headers=H["admin"]).status_code == 201
    r = live_client.post("/templates/sales-representative/instantiate", json=body, headers=H["operator"])
    assert r.status_code == 201 and r.json()["voice"]["profile_id"] == pid
    r = live_client.post("/templates/sales-representative/instantiate",
                         json={"overrides": {"name": "Ava", "company": "Safi", "voice.profile_id": "no-such-profile"}},
                         headers=H["operator"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "voice_profile_not_found"


@needs_engine
def test_live_preview_returns_valid_wav(live_client):
    pid = SEEDED["Ava Warm"]
    auth = {"consent_statement": "Synthetic seed voice, authorized by construction.", "subject_name": "Zira (synthetic)",
            "authorized_by": "test", "source": "live-test"}
    assert live_client.post(f"/voices/{pid}/authorizations", json=auth, headers=H["admin"]).status_code == 201
    r = live_client.post(f"/voices/{pid}/preview", json={"text": "Hello, nice to meet you."}, headers=H["operator"])
    assert r.status_code == 200, r.text[:300]
    assert r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getnchannels() == 1 and w.getframerate() == 24000
        assert w.getnframes() / w.getframerate() > 0.5, "expected at least half a second of speech"
