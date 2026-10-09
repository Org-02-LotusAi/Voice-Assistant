import pytest
from fastapi.testclient import TestClient

from service015.api.app import create_app
from service015.config import Settings
from tests.voice_fakes import FakeVoiceStack

KEYS = {"adm": "admin", "ope": "operator", "vie": "viewer"}
H = {role: {"Authorization": f"Bearer {key}"} for key, role in KEYS.items()}


def make_settings(db_path) -> Settings:
    # Voicebox / engine point at a closed port: tests never reach the real services unless they inject a client.
    return Settings(
        SERVICE015_API_KEYS="adm:admin,ope:operator,vie:viewer",
        SERVICE015_DB_PATH=str(db_path),
        VOICEBOX_URL="http://127.0.0.1:9",
        VOICE_ENGINE_URL="http://127.0.0.1:9",
        _env_file=None,
    )


def make_app(db_file, stack, supervisor=None):
    """App wired to the fake Voicebox/engine of `stack`."""
    return create_app(make_settings(db_file), supervisor, voicebox=stack.voicebox.client, voice_engine=stack.engine.client)


def voiced_identity(c, **extra):
    """Authorize the fake voice 'vp-ava' and instantiate Ava (ava-001) with it; returns the response."""
    auth = {"consent_statement": "Synthetic voice.", "subject_name": "Zira", "authorized_by": "ops", "source": "test"}
    assert c.post("/voices/vp-ava/authorizations", json=auth, headers=H["admin"]).status_code == 201
    return c.post("/templates/sales-representative/instantiate", headers=H["operator"],
                  json={"overrides": {"name": "Ava", "company": "Safi", "voice.profile_id": "vp-ava", **extra}})


@pytest.fixture
def db_file(tmp_path):
    return tmp_path / "api.db"


@pytest.fixture
def voice_stack():
    return FakeVoiceStack()


@pytest.fixture
def client(db_file, voice_stack):
    with TestClient(make_app(db_file, voice_stack)) as c:
        yield c


@pytest.fixture
def headers():
    return H


class FakeSupervisor:
    """Supervisor returning ParticipantStatus-shaped dicts; `explode=True` makes launch raise."""

    def __init__(self, explode: bool = False) -> None:
        self.explode = explode

    def _status(self, identity_id, presence="OFFLINE"):
        return {
            "identity_id": identity_id, "participant_identity": identity_id, "default_room": identity_id,
            "presence": presence, "status_detail": None, "last_error": None,
            "capabilities": {"audio_receive": True, "audio_publish": True, "barge_in": True, "video_publish": False},
            "launched_at": "2026-01-01T00:00:00+00:00" if presence != "OFFLINE" else None, "heartbeat_at": None,
        }

    async def launch(self, identity_id):
        if self.explode:
            raise RuntimeError("boom")
        return self._status(identity_id, "STARTING")

    async def stop(self, identity_id):
        return self._status(identity_id)

    async def status(self, identity_id):
        return self._status(identity_id)
