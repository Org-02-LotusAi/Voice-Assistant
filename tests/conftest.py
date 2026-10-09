import pytest
from fastapi.testclient import TestClient

from service015.api.app import create_app
from service015.config import Settings

KEYS = {"adm": "admin", "ope": "operator", "vie": "viewer"}
H = {role: {"Authorization": f"Bearer {key}"} for key, role in KEYS.items()}


def make_settings(db_path) -> Settings:
    return Settings(SERVICE015_API_KEYS="adm:admin,ope:operator,vie:viewer", SERVICE015_DB_PATH=str(db_path), _env_file=None)


@pytest.fixture
def db_file(tmp_path):
    return tmp_path / "api.db"


@pytest.fixture
def client(db_file):
    with TestClient(create_app(make_settings(db_file))) as c:
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
