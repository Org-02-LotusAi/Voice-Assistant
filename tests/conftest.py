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
