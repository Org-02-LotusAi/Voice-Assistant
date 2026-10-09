import os

import httpx
import pytest

ENGINE_URL = os.environ.get("VOICE_ENGINE_URL", "").rstrip("/")


def _reachable() -> bool:
    if not ENGINE_URL:
        return False
    try:
        return httpx.get(f"{ENGINE_URL}/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def pytest_collection_modifyitems(config, items):
    if _reachable():
        return
    skip = pytest.mark.skip(reason="VOICE_ENGINE_URL not set or engine not reachable")
    for item in items:
        if "live_engine" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def engine_url() -> str:
    return ENGINE_URL


@pytest.fixture(scope="session")
def profile_id() -> str:
    return os.environ.get("VOICE_ENGINE_PROFILE_ID", "6326829e-38ae-475f-aedb-5c293aa43e51")


def pytest_configure(config):
    config.addinivalue_line("markers", "live_engine: needs a running voice engine (VOICE_ENGINE_URL)")
