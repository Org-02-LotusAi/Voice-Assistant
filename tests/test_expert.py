import json
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import H, make_settings
from service015.api.app import create_app
from service015.db import connect, migrate
from service015.errors import ValidationFailed
from service015.expert import qa, store
from service015.expert.bm25 import BM25, tokenize
from service015.expert.llm import LlmUnavailable, OpenAICompatLlm
from test_openapi_contract import assert_conforms

REPO_ROOT = Path(__file__).resolve().parents[1]
PROV = store.Provenance("repo-x", "main", "0123456789abcdef0123456789abcdef01234567")
FACT_TITLE = "Chatterbox warm-up"
FACT_BODY = "The first Chatterbox synthesis needs a warm-up of exactly 7 seconds (ZQX-91) before real-time use."
FACT_SOURCE = "tests/test_voice_warmup.py"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "e.db")
    migrate(c)
    yield c
    c.close()


class FakeLlm:
    """Records the messages it gets; answers by citing every item whose text is in the system prompt."""

    def __init__(self):
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages):
        self.calls.append(messages)
        system = messages[0]["content"]
        cites = [line.split("cite as ")[1].split(" |")[0] for line in system.splitlines() if "cite as " in line]
        return "Answer using " + " ".join(cites)


def _write_tree(root: Path):
    (root / "docs").mkdir()
    (root / "docs" / "SOW.md").write_text("# SOW\n" + ("Voice identities are persistent participants. " * 60), encoding="utf-8")
    (root / "docs" / "SECRET.md").write_text("Voice authorization provenance: consent from Jane Roe, case ADMINONLY-77.", encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets" / "notes.md").write_text("raw sample notes", encoding="utf-8")
    (root / "voice.wav").write_bytes(b"RIFF\x00\x00")
    manifest = {
        "service": "t",
        "chunking": {"size": 300, "overlap": 50},
        "sources": [
            {"id": "sow", "path": "docs/SOW.md", "min_role": "viewer"},
            {"id": "secret", "path": "docs/SECRET.md", "min_role": "admin"},
            {"id": "assets", "path": "assets/*.md", "min_role": "viewer"},
            {"id": "wav", "path": "*.wav", "min_role": "viewer"},
            {"id": "opt", "path": "README.md", "min_role": "viewer", "optional": True},
        ],
        "never_ingest": {"suffixes": [".wav"], "directories": ["assets"]},
    }
    import yaml

    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root / "manifest.yaml"


# ---------- chunking / BM25 ----------

def test_chunk_text_size_overlap_and_coverage():
    text = " ".join(f"word{i}" for i in range(600))
    chunks = store.chunk_text(text, 800, 150)
    assert len(chunks) > 3
    assert all(len(c) <= 800 for c in chunks)
    # consecutive chunks overlap
    for a, b in zip(chunks, chunks[1:]):
        assert set(a.split()[-5:]) & set(b.split()[:30])
    assert chunks[0].startswith("word0") and chunks[-1].endswith("word599")
    assert store.chunk_text("   ") == []
    assert store.chunk_text("short") == ["short"]


def test_bm25_ranks_relevant_document_first_and_ignores_stopwords():
    docs = ["the cat sat on the mat", "warm up of chatterbox takes seven seconds", "livekit rooms and participants"]
    scores = BM25(docs).scores("what is the chatterbox warm up")
    assert scores.index(max(scores)) == 1
    assert scores[0] == 0.0 and scores[2] == 0.0
    assert tokenize("The Warm-up, 7 SECONDS!") == ["warm", "up", "7", "seconds"]
    assert BM25([]).scores("x") == []


# ---------- ingestion ----------

def test_ingest_provenance_chunks_exclusions_and_replace(conn, tmp_path):
    mpath = _write_tree(tmp_path)
    # a missing non-optional source (wav is excluded but exists; assets/*.md exists) -> all present
    result = store.ingest_manifest(conn, tmp_path, mpath, provenance=PROV)
    assert result["sha"] == PROV.sha and result["branch"] == "main"
    assert result["ingested"]["docs/SOW.md"] > 1
    skipped = {s["source"] for s in result["skipped"]}
    assert {"assets/notes.md", "voice.wav", "README.md"} <= skipped
    rows = conn.execute("SELECT * FROM expert_knowledge").fetchall()
    assert rows and all(r["kind"] == "document" and r["sha"] == PROV.sha and r["repo"] == "repo-x" for r in rows)
    assert not [r for r in rows if r["source"] in ("assets/notes.md", "voice.wav")]
    assert {r["min_role"] for r in rows if r["source"] == "docs/SECRET.md"} == {"admin"}

    exp = store.add_experience(conn, FACT_TITLE, FACT_BODY, FACT_SOURCE, PROV)
    before = conn.execute("SELECT COUNT(*) FROM expert_knowledge WHERE kind = 'document'").fetchone()[0]
    (tmp_path / "docs" / "SOW.md").write_text("# SOW\nshort now", encoding="utf-8")
    store.ingest_manifest(conn, tmp_path, mpath, provenance=PROV)
    after = conn.execute("SELECT COUNT(*) FROM expert_knowledge WHERE kind = 'document'").fetchone()[0]
    assert after < before  # SOW chunks replaced, not appended
    assert conn.execute("SELECT COUNT(*) FROM expert_knowledge WHERE source = 'docs/SOW.md'").fetchone()[0] == 1
    survivor = conn.execute("SELECT * FROM expert_knowledge WHERE id = ?", (exp["id"],)).fetchone()
    assert survivor["kind"] == "verified_experience" and survivor["body"] == FACT_BODY


def test_missing_required_source_raises_and_writes_nothing(conn, tmp_path):
    mpath = _write_tree(tmp_path)
    (tmp_path / "docs" / "SECRET.md").unlink()
    with pytest.raises(ValidationFailed) as e:
        store.ingest_manifest(conn, tmp_path, mpath, provenance=PROV)
    assert e.value.code == "source_missing"
    assert conn.execute("SELECT COUNT(*) FROM expert_knowledge").fetchone()[0] == 0


def test_provenance_resolved_by_git_rev_parse():
    prov = store.resolve_provenance(REPO_ROOT)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()
    assert prov.sha == head and len(prov.sha) == 40 and prov.branch


def test_provenance_outside_a_repo_fails_loudly(tmp_path):
    with pytest.raises(ValidationFailed) as e:
        store.resolve_provenance(tmp_path)
    assert e.value.code == "provenance_unresolved"


def test_real_manifest_loads_and_ingests_repo(conn):
    result = store.ingest_manifest(conn, REPO_ROOT)
    assert "docs/SOW.md" in result["ingested"] and "openapi.yaml" in result["ingested"]
    assert any(s.startswith("src/service015/starter_templates/") for s in result["ingested"])
    assert not any(s.endswith(".wav") for s in result["ingested"])
    assert len(result["sha"]) == 40


# ---------- retrieval / RBAC ----------

def test_retrieval_is_role_filtered_and_prefers_verified_experience(conn, tmp_path):
    mpath = _write_tree(tmp_path)
    store.ingest_manifest(conn, tmp_path, mpath, provenance=PROV)
    store.add_experience(conn, FACT_TITLE, FACT_BODY, FACT_SOURCE, PROV)
    viewer_hits = qa.retrieve(conn, "voice authorization provenance consent ADMINONLY-77", "viewer")
    assert not [h for h in viewer_hits if h["source"] == "docs/SECRET.md"]
    admin_hits = qa.retrieve(conn, "voice authorization provenance consent ADMINONLY-77", "admin")
    assert admin_hits[0]["source"] == "docs/SECRET.md"
    hits = qa.retrieve(conn, "how long is the Chatterbox warm-up", "viewer")
    assert hits[0]["kind"] == "verified_experience"
    assert qa.retrieve(conn, "zzzz qqqq", "viewer") == []


def test_viewer_cannot_see_admin_knowledge_in_listing(conn):
    store.add_experience(conn, "public", "p body", "s", PROV)
    store.add_experience(conn, "private", "secret body", "s", PROV, min_role="admin")
    assert [m["title"] for m in store.list_metadata(conn, "viewer")] == ["public"]
    assert [m["title"] for m in store.list_metadata(conn, "admin")] == ["public", "private"]
    assert all("body" not in m for m in store.list_metadata(conn, "admin"))


# ---------- ask (LLM mocked) ----------

def test_ask_uses_verified_fact_cites_it_and_fresh_session_has_no_history(conn):
    store.add_experience(conn, FACT_TITLE, FACT_BODY, FACT_SOURCE, PROV)
    llm = FakeLlm()
    s1 = store.create_session(conn, "viewer")
    r1 = qa.ask(conn, s1, "How long is the Chatterbox warm-up?", "viewer", llm=llm)
    assert FACT_BODY in llm.calls[0][0]["content"]
    assert "VERIFIED EXPERIENCE" in llm.calls[0][0]["content"]
    assert [c["kind"] for c in r1["citations"]] == ["verified_experience"]
    assert r1["citations"][0]["sha"] == PROV.sha and r1["citations"][0]["source"] == FACT_SOURCE

    # follow-up in the same session carries that session's history
    qa.ask(conn, s1, "And the exact constant?", "viewer", llm=llm)
    assert any("How long is the Chatterbox warm-up?" == m["content"] for m in llm.calls[1])

    # fresh session: empty history, yet the knowledge is retrieved from the store
    s2 = store.create_session(conn, "viewer")
    assert s2 != s1
    r2 = qa.ask(conn, s2, "How long is the Chatterbox warm-up?", "viewer", llm=llm)
    msgs = llm.calls[2]
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert FACT_BODY in msgs[0]["content"]
    assert r2["citations"][0]["kind"] == "verified_experience"
    assert conn.execute("SELECT COUNT(*) FROM expert_messages WHERE session_id = ?", (s2,)).fetchone()[0] == 2


def test_ask_without_knowledge_does_not_call_llm(conn):
    llm = FakeLlm()
    sid = store.create_session(conn, "viewer")
    out = qa.ask(conn, sid, "qqqq zzzz", "viewer", llm=llm)
    assert out["answer"] == qa.NO_KNOWLEDGE and out["citations"] == [] and llm.calls == []


def test_ask_unknown_session_and_role_mismatch(conn):
    from service015.auth import Forbidden
    from service015.errors import NotFound

    with pytest.raises(NotFound):
        qa.ask(conn, "nope", "q", "viewer", llm=FakeLlm())
    sid = store.create_session(conn, "admin")
    with pytest.raises(Forbidden):
        qa.ask(conn, sid, "q", "viewer", llm=FakeLlm())


def test_llm_failure_stores_nothing(conn):
    class Down:
        def chat(self, messages):
            raise LlmUnavailable("down")

    store.add_experience(conn, FACT_TITLE, FACT_BODY, FACT_SOURCE, PROV)
    sid = store.create_session(conn, "viewer")
    with pytest.raises(LlmUnavailable):
        qa.ask(conn, sid, "Chatterbox warm-up?", "viewer", llm=Down())
    assert conn.execute("SELECT COUNT(*) FROM expert_messages").fetchone()[0] == 0


# ---------- OpenAI-compatible client (httpx.MockTransport) ----------

def _llm(handler, key="k-123"):
    return OpenAICompatLlm("https://llm.test/v1", key, "m-1", transport=httpx.MockTransport(handler))


def test_llm_client_request_shape_and_parse():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": " hi "}}]})

    assert _llm(handler).chat([{"role": "user", "content": "x"}]) == "hi"
    assert seen["url"] == "https://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer k-123"
    assert seen["body"]["model"] == "m-1" and seen["body"]["messages"] == [{"role": "user", "content": "x"}]


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")),
        lambda r: httpx.Response(500, json={}),
        lambda r: httpx.Response(200, json={"nope": 1}),
        lambda r: httpx.Response(200, text="not json"),
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]}),
    ],
    ids=["unreachable", "http500", "bad-shape", "not-json", "empty"],
)
def test_llm_client_failures_raise_llm_unavailable(handler):
    with pytest.raises(LlmUnavailable) as e:
        _llm(handler).chat([{"role": "user", "content": "x"}])
    assert e.value.code == "llm_unavailable" and e.value.status == 503
    assert "k-123" not in e.value.message


def test_llm_client_without_key_does_not_call_network():
    def handler(request):
        raise AssertionError("must not be called")

    with pytest.raises(LlmUnavailable):
        _llm(handler, key="").chat([{"role": "user", "content": "x"}])


# ---------- API ----------

@pytest.fixture
def api(db_file):
    llm = FakeLlm()
    with TestClient(create_app(make_settings(db_file), expert_llm=llm)) as c:
        c.llm = llm
        yield c


def _experience(**over):
    return {"title": FACT_TITLE, "body": FACT_BODY, "source": FACT_SOURCE, "repo": "repo-x", "branch": "main", "sha": PROV.sha, **over}


def test_api_fresh_learning_flow(api):
    r = api.post("/expert/experience", json=_experience(), headers=H["admin"])
    assert r.status_code == 201, r.text
    assert_conforms("/expert/experience", "post", r)
    item = r.json()
    assert item["kind"] == "verified_experience" and item["sha"] == PROV.sha and "body" not in item

    sid = api.post("/expert/sessions", headers=H["viewer"])
    assert sid.status_code == 201
    assert_conforms("/expert/sessions", "post", sid)
    ask = api.post(f"/expert/sessions/{sid.json()['session_id']}/ask",
                   json={"question": "How long is the Chatterbox warm-up?"}, headers=H["viewer"])
    assert ask.status_code == 200, ask.text
    assert_conforms("/expert/sessions/{session_id}/ask", "post", ask)
    body = ask.json()
    assert [c["id"] for c in body["citations"]] == [item["id"]]
    assert FACT_BODY in api.llm.calls[-1][0]["content"]

    listing = api.get("/expert/knowledge", headers=H["viewer"])
    assert listing.status_code == 200 and listing.json()[0]["id"] == item["id"]
    assert_conforms("/expert/knowledge", "get", listing)


def test_api_rbac_and_errors(api):
    assert api.post("/expert/experience", json=_experience(), headers=H["operator"]).status_code == 403
    assert api.post("/expert/experience", json=_experience(sha=None), headers=H["admin"]).status_code == 422  # partial provenance
    api.post("/expert/experience", json=_experience(title="private", body="private body", min_role="admin"), headers=H["admin"])
    assert api.get("/expert/knowledge", headers=H["viewer"]).json() == []
    assert len(api.get("/expert/knowledge", headers=H["admin"]).json()) == 1

    r = api.post("/expert/sessions/nope/ask", json={"question": "q"}, headers=H["viewer"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "session_not_found"
    assert_conforms("/expert/sessions/{session_id}/ask", "post", r)

    admin_sid = api.post("/expert/sessions", headers=H["admin"]).json()["session_id"]
    r = api.post(f"/expert/sessions/{admin_sid}/ask", json={"question": "q"}, headers=H["viewer"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert api.post(f"/expert/sessions/{admin_sid}/ask", json={"question": ""}, headers=H["admin"]).status_code == 422


def test_api_experience_resolves_provenance_from_git_when_omitted(api, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    r = api.post("/expert/experience", json={"title": "t", "body": "b", "source": "s"}, headers=H["admin"])
    assert r.status_code == 201, r.text
    assert len(r.json()["sha"]) == 40


def test_api_llm_unavailable_is_503_not_a_fabricated_answer(db_file):
    # real client, no key configured -> 503 llm_unavailable
    with TestClient(create_app(make_settings(db_file))) as c:
        c.post("/expert/experience", json=_experience(), headers=H["admin"])
        sid = c.post("/expert/sessions", headers=H["viewer"]).json()["session_id"]
        r = c.post(f"/expert/sessions/{sid}/ask", json={"question": "Chatterbox warm-up?"}, headers=H["viewer"])
        assert r.status_code == 503 and r.json()["error"]["code"] == "llm_unavailable"
        assert "answer" not in r.json()
        assert_conforms("/expert/sessions/{session_id}/ask", "post", r)


def test_knowledge_survives_app_restart(db_file):
    with TestClient(create_app(make_settings(db_file), expert_llm=FakeLlm())) as c:
        c.post("/expert/experience", json=_experience(), headers=H["admin"])
    llm = FakeLlm()
    with TestClient(create_app(make_settings(db_file), expert_llm=llm)) as c:  # fresh process state
        sid = c.post("/expert/sessions", headers=H["viewer"]).json()["session_id"]
        r = c.post(f"/expert/sessions/{sid}/ask", json={"question": "Chatterbox warm-up?"}, headers=H["viewer"])
        assert r.status_code == 200 and r.json()["citations"][0]["kind"] == "verified_experience"
