"""TEST 15 against the real LLM. Skipped unless LLM_API_KEY is set (env or .env)."""

import pytest
from fastapi.testclient import TestClient

from tests.conftest import H, make_settings
from service015.api.app import create_app
from service015.config import Settings

LIVE = Settings(_env_file=".env")

pytestmark = pytest.mark.skipif(not LIVE.llm_api_key, reason="LLM_API_KEY not set")


def test_fresh_session_uses_new_verified_fact(db_file):
    # Synthetic fact (not a real finding): only the store can supply the value 'ZQX-4417 / 11 seconds'.
    fact = "Integration-test fact: the Quasar pre-roll buffer constant ZQX-4417 must be set to 11 seconds."
    settings = make_settings(db_file).model_copy(
        update={"llm_api_key": LIVE.llm_api_key, "llm_base_url": LIVE.llm_base_url, "llm_model": LIVE.llm_model,
                "llm_reasoning_effort": LIVE.llm_reasoning_effort}
    )
    with TestClient(create_app(settings)) as c:
        r = c.post(
            "/expert/experience",
            json={"title": "Quasar pre-roll buffer", "body": fact, "source": "tests/test_expert_live.py",
                  "repo": "voicesow", "branch": "main", "sha": "0123456789abcdef"},
            headers=H["admin"],
        )
        assert r.status_code == 201
        sid = c.post("/expert/sessions", headers=H["viewer"]).json()["session_id"]
        a = c.post(f"/expert/sessions/{sid}/ask",
                   json={"question": "What must the Quasar pre-roll buffer constant ZQX-4417 be set to?"},
                   headers=H["viewer"])
        assert a.status_code == 200, a.text
        body = a.json()
        assert "11" in body["answer"]
        assert any(cit["kind"] == "verified_experience" for cit in body["citations"])
