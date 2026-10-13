"""Switchable Deepgram STT/TTS provider (task D1): config validation, voices, preflight, worker wiring, secret hygiene.

Everything here is offline. The live checks at the bottom skip without a real DEEPGRAM_API_KEY.
"""

import asyncio
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from service015 import identities_repo
from service015.api.app import create_app
from service015.config import DEEPGRAM_KEY_PLACEHOLDER, Settings
from service015.errors import DependencyUnavailable, ValidationFailed
from service015.runtime.procs import allowed_env
from service015.runtime.supervisor import LiveKitSupervisor, worker_env
from service015.voice import deepgram as dg
from service015.voice.deepgram import DeepgramSpeakClient
from tests.conftest import FakeSupervisor, H, make_app, make_settings
from tests.test_openapi_contract import assert_conforms
from tests.test_runtime import FakeLK, FakeWorker, _llm_transport, _settings, conn, stack  # noqa: F401  (fixtures)
from tests.voice_fakes import make_wav

KEY = "dg-test-key-0123456789abcdef"
DG = {"SERVICE015_TTS_PROVIDER": "deepgram", "DEEPGRAM_API_KEY": KEY}
THALIA = "deepgram:aura-2-thalia-en"


def code(r):
    return r.json()["error"]["code"]


def settings(**kw) -> Settings:
    return Settings(SERVICE015_API_KEYS="adm:admin", _env_file=None, **kw)


# ---------- config ----------


def test_defaults_are_local_and_need_no_key():
    s = settings()
    assert (s.stt_provider, s.tts_provider) == ("local", "local")
    assert s.deepgram_stt_model == "nova-3" and s.deepgram_stt_language == ""
    assert not s.uses_deepgram and not s.deepgram_key_configured


@pytest.mark.parametrize("var", ["SERVICE015_STT_PROVIDER", "SERVICE015_TTS_PROVIDER"])
@pytest.mark.parametrize("key", [None, "", "   ", DEEPGRAM_KEY_PLACEHOLDER])
def test_deepgram_provider_without_a_real_key_fails_naming_the_variable(var, key):
    extra = {} if key is None else {"DEEPGRAM_API_KEY": key}
    with pytest.raises(ValidationError) as exc:
        settings(**{var: "deepgram"}, **extra)
    assert "DEEPGRAM_API_KEY" in str(exc.value)


def test_deepgram_provider_with_a_key_is_accepted():
    s = settings(SERVICE015_STT_PROVIDER="deepgram", DEEPGRAM_API_KEY=KEY)
    assert s.uses_deepgram and s.deepgram_key_configured and s.tts_provider == "local"


def test_placeholder_key_is_fine_while_providers_stay_local():
    assert not settings(DEEPGRAM_API_KEY=DEEPGRAM_KEY_PLACEHOLDER).deepgram_key_configured


def test_unknown_provider_value_is_rejected_without_echoing_the_key():
    with pytest.raises(ValidationError) as exc:
        settings(SERVICE015_TTS_PROVIDER="elevenlabs", DEEPGRAM_API_KEY=KEY)
    assert KEY not in str(exc.value) and "SERVICE015_TTS_PROVIDER" in str(exc.value)


def test_key_is_not_in_repr_or_str():
    s = settings(**DG)
    assert KEY not in repr(s) and KEY not in str(s)


def test_missing_key_error_does_not_contain_other_secrets():
    with pytest.raises(ValidationError) as exc:
        settings(SERVICE015_TTS_PROVIDER="deepgram", LLM_API_KEY="llm-secret-value")
    assert "llm-secret-value" not in str(exc.value)


# ---------- worker environment ----------


def test_worker_env_carries_providers_and_the_key_only_when_needed():
    local = worker_env(settings())
    assert local["SERVICE015_STT_PROVIDER"] == "local" and local["SERVICE015_TTS_PROVIDER"] == "local"
    assert "DEEPGRAM_API_KEY" not in local
    dgs = worker_env(settings(**DG, SERVICE015_DEEPGRAM_STT_MODEL="nova-3-general"))
    assert dgs["DEEPGRAM_API_KEY"] == KEY and dgs["SERVICE015_TTS_PROVIDER"] == "deepgram"
    assert dgs["SERVICE015_DEEPGRAM_STT_MODEL"] == "nova-3-general"


def test_parent_env_deepgram_key_is_not_inherited_by_the_allow_list():
    assert "DEEPGRAM_API_KEY" not in allowed_env({"DEEPGRAM_API_KEY": KEY, "PATH": "x"})


# ---------- voice catalog ----------


def test_catalog_is_well_formed():
    assert len(dg.AURA_2_ENGLISH) == 41
    ids = [v.voice_id for v in dg.AURA_2_ENGLISH]
    assert len(set(ids)) == len(ids) and all(i.startswith("deepgram:aura-2-") and i.endswith("-en") for i in ids)
    assert dg.find_voice(THALIA).model == "aura-2-thalia-en" and dg.model_of(THALIA) == "aura-2-thalia-en"
    assert dg.find_voice("deepgram:aura-2-nobody-en") is None and dg.find_voice("vp-ava") is None
    assert dg.find_voice(THALIA).display_name == "Thalia (feminine, American)"


def test_catalog_models_known_to_the_plugin_use_its_spelling():
    from typing import get_args

    from livekit.plugins.deepgram.models import TTSModels

    plugin = set(get_args(TTSModels))
    # the plugin's list is older than Deepgram's docs; every catalog voice it does know is spelled identically
    assert {v.model for v in dg.AURA_2_ENGLISH} & plugin


# ---------- API in Deepgram mode ----------


@pytest.fixture
def dg_client(db_file, voice_stack):
    with TestClient(make_app(db_file, voice_stack, FakeSupervisor(), **DG)) as c:
        yield c


def test_voices_lists_the_catalog_without_touching_voicebox(dg_client, voice_stack):
    voice_stack.voicebox.down = True  # must not matter
    r = dg_client.get("/voices", headers=H["viewer"])
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 41 and {v["engine"] for v in body} == {"deepgram-aura-2"}
    assert all(v["authorized"] and v["shared"] and not v["owned_by_me"] for v in body)
    assert THALIA in {v["voice_profile_id"] for v in body}
    assert_conforms("/voices", "get", r)


def test_upload_is_refused_before_the_body_is_read(dg_client):
    r = dg_client.post("/voices", headers=H["admin"], data={"name": "x"})  # an incomplete form would otherwise be 422
    assert r.status_code == 409 and code(r) == "voice_cloning_unavailable"
    assert r.json()["error"]["details"] == {"tts_provider": "deepgram"}
    assert_conforms("/voices", "post", r)


def test_upload_rejection_still_requires_authentication(dg_client):
    assert dg_client.post("/voices", data={"name": "x"}).status_code == 401


def test_deepgram_voices_cannot_be_authorized_or_patched(dg_client):
    auth = {"consent_statement": "c", "subject_name": "s", "authorized_by": "a", "source": "t"}
    r = dg_client.post(f"/voices/{THALIA}/authorizations", json=auth, headers=H["admin"])
    assert r.status_code == 422 and code(r) == "voice_profile_not_cloned"
    r = dg_client.patch(f"/voices/{THALIA}", json={"disabled": True}, headers=H["admin"])
    assert r.status_code == 422 and code(r) == "voice_profile_not_cloned"


def test_admin_voice_list_is_empty_in_deepgram_mode(dg_client, voice_stack):
    voice_stack.voicebox.down = True
    r = dg_client.get("/admin/voices", headers=H["admin"])
    assert r.status_code == 200 and r.json() == []


def instantiate(c, voice):
    return c.post("/templates/sales-representative/instantiate", headers=H["operator"],
                  json={"overrides": {"name": "Ava", "company": "Safi", "voice.profile_id": voice}})


def test_instantiate_accepts_a_catalog_voice_and_rejects_others(dg_client):
    r = instantiate(dg_client, "deepgram:aura-2-nobody-en")
    assert r.status_code == 422 and code(r) == "voice_profile_not_found"
    r = instantiate(dg_client, "vp-ava")  # a cloned voice
    assert r.status_code == 422 and code(r) == "voice_provider_mismatch"
    assert r.json()["error"]["details"]["tts_provider"] == "deepgram"
    r = instantiate(dg_client, THALIA)
    assert r.status_code == 201 and r.json()["voice"]["profile_id"] == THALIA


def test_patch_to_a_cloned_voice_is_refused_in_deepgram_mode(dg_client):
    assert instantiate(dg_client, THALIA).status_code == 201
    r = dg_client.patch("/identities/ava-001", headers=H["operator"], json={"overrides": {"voice.profile_id": "vp-ava"}})
    assert r.status_code == 422 and code(r) == "voice_provider_mismatch"
    ok = dg_client.patch("/identities/ava-001", headers=H["operator"],
                         json={"overrides": {"voice.profile_id": "deepgram:aura-2-zeus-en"}})
    assert ok.status_code == 200


def test_local_mode_refuses_deepgram_voices(client):
    r = instantiate(client, THALIA)
    assert r.status_code == 422 and code(r) == "voice_provider_mismatch"
    assert r.json()["error"]["details"]["tts_provider"] == "local"
    assert_conforms("/templates/{template_id}/instantiate", "post", r)


def test_local_mode_voice_list_is_unchanged(client):
    assert {v["engine"] for v in client.get("/voices", headers=H["viewer"]).json()} == {"chatterbox-turbo"}


def test_launch_of_a_persona_bound_to_a_cloned_voice_fails_cleanly_in_deepgram_mode(db_file, voice_stack):
    from tests.test_voices import AUTH

    class Spy(FakeSupervisor):
        launched = 0

        async def launch(self, identity_id):
            Spy.launched += 1
            return await super().launch(identity_id)

    with TestClient(make_app(db_file, voice_stack, Spy())) as local:  # set the persona up in local mode
        assert local.post("/voices/vp-ava/authorizations", json=AUTH, headers=H["admin"]).status_code == 201
        assert instantiate(local, "vp-ava").status_code == 201
    voice_stack.voicebox.down = True  # Deepgram mode must not need Voicebox to say no
    with TestClient(make_app(db_file, voice_stack, Spy(), **DG)) as c:
        r = c.post("/identities/ava-001/launch", headers=H["operator"])
        assert r.status_code == 422 and code(r) == "voice_provider_mismatch"
        assert_conforms("/identities/{identity_id}/launch", "post", r)
        assert Spy.launched == 0
    voice_stack.voicebox.down = False
    with TestClient(make_app(db_file, voice_stack, Spy())) as local:  # and the reverse after switching back
        r = local.patch("/identities/ava-001", headers=H["operator"], json={"overrides": {"voice.profile_id": THALIA}})
        assert r.status_code == 422 and code(r) == "voice_provider_mismatch"


def test_launch_of_a_catalog_voice_persona_reaches_the_supervisor(db_file, voice_stack):
    with TestClient(make_app(db_file, voice_stack, FakeSupervisor(), **DG)) as c:
        voice_stack.voicebox.down = True
        assert instantiate(c, THALIA).status_code == 201
        assert c.post("/identities/ava-001/launch", headers=H["operator"]).status_code == 200


def test_health_reports_providers_and_skips_local_probes(db_file, voice_stack):
    with TestClient(make_app(db_file, voice_stack, FakeSupervisor(), **DG)) as c:
        voice_stack.voicebox.down = voice_stack.engine.down = True
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["providers"] == {"stt": "local", "tts": "deepgram"}
        assert r.json()["dependencies"]["voicebox"] == r.json()["dependencies"]["voice_engine"] == "not_used"
        assert_conforms("/health", "get", r)


def test_health_in_local_mode_reports_local_providers(client):
    r = client.get("/health")
    assert r.json()["providers"] == {"stt": "local", "tts": "local"}
    assert_conforms("/health", "get", r)


# ---------- preview ----------


def speak_client(status=200, body=None):
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(status, content=make_wav() if body is None else body)

    c = DeepgramSpeakClient(KEY, transport=httpx.MockTransport(handle))
    return c, seen


def preview_app(db_file, voice_stack, speak):
    return TestClient(
        create_app(
            make_settings(db_file, **DG), FakeSupervisor(), voicebox=voice_stack.voicebox.client,
            voice_engine=voice_stack.engine.client, deepgram_speak=speak,
        )
    )


def test_preview_synthesizes_through_deepgram_rest(db_file, voice_stack):
    speak, seen = speak_client()
    with preview_app(db_file, voice_stack, speak) as c:
        r = c.post(f"/voices/{THALIA}/preview", headers=H["operator"], json={"text": "Hi there"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("audio/wav") and r.content[:4] == b"RIFF"
    assert_conforms("/voices/{voice_profile_id}/preview", "post", r)
    req = seen[0]
    assert req.url.host == "api.deepgram.com" and req.url.path == "/v1/speak"
    assert dict(req.url.params) == {"model": "aura-2-thalia-en", "encoding": "linear16", "container": "wav", "sample_rate": "24000"}
    assert req.headers["authorization"] == f"Token {KEY}"
    assert json.loads(req.content) == {"text": "Hi there"}


@pytest.mark.parametrize("status", [401, 403, 500])
def test_preview_deepgram_failure_is_503_and_never_leaks_the_key(db_file, voice_stack, status):
    speak, _ = speak_client(status, body=b"nope")
    with preview_app(db_file, voice_stack, speak) as c:
        r = c.post(f"/voices/{THALIA}/preview", headers=H["operator"])
    assert r.status_code == 503 and code(r) == "deepgram_unavailable"
    assert KEY not in r.text
    assert_conforms("/voices/{voice_profile_id}/preview", "post", r)


def test_preview_transport_error_is_503(db_file, voice_stack):
    def boom(req):
        raise httpx.ConnectError("no route", request=req)

    speak = DeepgramSpeakClient(KEY, transport=httpx.MockTransport(boom))
    with preview_app(db_file, voice_stack, speak) as c:
        r = c.post(f"/voices/{THALIA}/preview", headers=H["operator"])
    assert r.status_code == 503 and code(r) == "deepgram_unavailable" and KEY not in r.text


def test_preview_of_a_cloned_voice_in_deepgram_mode_is_a_mismatch(db_file, voice_stack):
    speak, seen = speak_client()
    with preview_app(db_file, voice_stack, speak) as c:
        r = c.post("/voices/vp-ava/preview", headers=H["operator"])
    assert r.status_code == 422 and code(r) == "voice_provider_mismatch" and not seen


def test_preview_of_a_deepgram_voice_in_local_mode_is_a_mismatch(client):
    r = client.post(f"/voices/{THALIA}/preview", headers=H["operator"])
    assert r.status_code == 422 and code(r) == "voice_provider_mismatch"


# ---------- secret hygiene across responses ----------


def test_key_never_appears_in_any_response_body(dg_client):
    bodies = [
        dg_client.get("/health").text,
        dg_client.get("/voices", headers=H["viewer"]).text,
        dg_client.post("/voices", headers=H["admin"], data={"name": "x"}).text,
        instantiate(dg_client, "vp-ava").text,
        dg_client.get("/openapi.yaml").text,
    ]
    assert all(KEY not in b for b in bodies)


# ---------- supervisor preflight ----------


def dg_supervisor(conn, stack, **kw):
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    lk, worker = FakeLK(), FakeWorker()
    sup = LiveKitSupervisor(
        _settings(path, **DG, **kw), engine=stack.engine.client, llm_transport=_llm_transport(200),
        api_factory=lambda: lk, worker=worker, worker_ready_timeout_s=2,
    )
    return sup, lk, worker


def test_preflight_in_deepgram_mode_does_not_need_engine_or_voicebox(conn, stack):
    identities_repo.patch_identity(conn, "ava-001", {"voice.profile_id": THALIA})
    stack.engine.down = stack.voicebox.down = True
    sup, lk, worker = dg_supervisor(conn, stack)
    st = asyncio.run(sup.launch("ava-001"))
    assert st["presence"] == "STARTING" and worker.spawned == 1
    assert [c for c in lk.calls if c[0] == "create_dispatch"]


def test_preflight_in_deepgram_mode_rejects_a_cloned_voice_before_touching_anything(conn, stack):
    sup, lk, worker = dg_supervisor(conn, stack)  # ava-001 is bound to the cloned vp-ava
    with pytest.raises(ValidationFailed) as exc:
        asyncio.run(sup.launch("ava-001"))
    assert exc.value.code == "voice_provider_mismatch" and worker.spawned == 0 and lk.calls == []


def test_preflight_in_local_mode_rejects_a_deepgram_voice(conn, stack):
    identities_repo.patch_identity(conn, "ava-001", {"voice.profile_id": THALIA})
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    sup = LiveKitSupervisor(
        _settings(path), engine=stack.engine.client, llm_transport=_llm_transport(200), api_factory=lambda: FakeLK(),
        worker=FakeWorker(), worker_ready_timeout_s=2,
    )
    with pytest.raises(ValidationFailed) as exc:
        asyncio.run(sup.launch("ava-001"))
    assert exc.value.code == "voice_provider_mismatch"


def test_preflight_in_deepgram_mode_still_checks_livekit_and_llm(conn, stack):
    identities_repo.patch_identity(conn, "ava-001", {"voice.profile_id": THALIA})
    sup, _, _ = dg_supervisor(conn, stack, llm_key="")
    with pytest.raises(DependencyUnavailable) as exc:
        asyncio.run(sup.launch("ava-001"))
    assert exc.value.details["dependency"] == "llm"


# ---------- worker wiring ----------


def worker_resolved(profile_id, language="en-US"):
    from types import SimpleNamespace

    return SimpleNamespace(voice=SimpleNamespace(profile_id=profile_id), self_=SimpleNamespace(language=language))


def test_build_stt_selects_the_provider():
    from livekit.plugins import deepgram

    from service015.runtime.worker import build_stt
    from service015.stt.moonshine import MoonshineSTT

    proc = type("P", (), {"userdata": {"transcriber": object()}})()
    assert isinstance(build_stt(settings(), worker_resolved("vp-ava"), proc), MoonshineSTT)
    s = build_stt(settings(SERVICE015_STT_PROVIDER="deepgram", DEEPGRAM_API_KEY=KEY), worker_resolved("vp", "en-GB"), proc)
    assert isinstance(s, deepgram.STT) and s.model == "nova-3"
    assert s.capabilities.streaming and s.capabilities.interim_results
    assert s._opts.language == "en" and s._opts.endpointing_ms == 25 and s._opts.interim_results


def test_build_stt_language_override_and_model():
    from service015.runtime.worker import build_stt

    s = build_stt(
        settings(SERVICE015_STT_PROVIDER="deepgram", DEEPGRAM_API_KEY=KEY, SERVICE015_DEEPGRAM_STT_LANGUAGE="multi",
                 SERVICE015_DEEPGRAM_STT_MODEL="nova-3-multilingual"),
        worker_resolved("vp", "hi-IN"), None,
    )
    assert s._opts.language == "multi" and s.model == "nova-3-multilingual"


def test_build_tts_selects_the_provider_and_reports_first_audio():
    from service015.runtime.latency import LatencyRecorder
    from service015.runtime.worker import build_tts
    from service015.voice.deepgram_tts import AuraTTS
    from service015.voice.livekit_tts import ChatterboxTTS

    rec = LatencyRecorder(lambda: None, "i", "s")
    local = build_tts(settings(), worker_resolved("vp-ava"), rec)
    assert isinstance(local, ChatterboxTTS) and local.profile_id == "vp-ava"
    t = build_tts(settings(**DG), worker_resolved(THALIA), rec)
    assert isinstance(t, AuraTTS) and t.model == "aura-2-thalia-en" and t.capabilities.streaming
    assert t.on_first_audio == rec.on_tts_first_audio and t.provider == "deepgram"


def test_aura_tts_reports_first_audio_once_per_segment(monkeypatch):
    from livekit.plugins.deepgram import tts as dgtts

    from service015.voice.deepgram_tts import _FirstAudioEmitter

    pushed, stamps = [], []

    class Inner:
        def push(self, data):
            pushed.append(data)

        def flush(self):
            pushed.append("flush")

    em = _FirstAudioEmitter(Inner(), stamps.append)
    em.push(b"")  # nothing received yet: not "first audio"
    em.push(b"\x00\x01")
    em.push(b"\x02\x03")
    em.flush()  # other emitter methods are delegated
    assert len(stamps) == 1 and pushed == [b"", b"\x00\x01", b"\x02\x03", "flush"]

    async def run():
        from service015.voice.deepgram_tts import AuraSynthesizeStream, AuraTTS

        seen = []

        async def parent_run_ws(self, word_stream, output_emitter):
            output_emitter.push(b"\x00\x00")
            seen.append(type(output_emitter).__name__)

        monkeypatch.setattr(dgtts.SynthesizeStream, "_run_ws", parent_run_ws)
        t = AuraTTS(model="aura-2-thalia-en", api_key=KEY, on_first_audio=stamps.append)
        s = t.stream()
        await AuraSynthesizeStream._run_ws(s, None, Inner())
        await s.aclose()
        return seen

    assert asyncio.run(run()) == ["_FirstAudioEmitter"] and len(stamps) == 2


def test_callback_exception_does_not_stop_audio(caplog):
    from service015.voice.deepgram_tts import _FirstAudioEmitter

    got = []

    class Inner:
        def push(self, data):
            got.append(data)

    def bad(_):
        raise RuntimeError("telemetry broke")

    _FirstAudioEmitter(Inner(), bad).push(b"ab")
    assert got == [b"ab"] and "on_first_audio callback raised" in caplog.text


# ---------- live (needs a real key) ----------

LIVE_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
live = pytest.mark.skipif(
    not LIVE_KEY or LIVE_KEY == DEEPGRAM_KEY_PLACEHOLDER, reason="no real DEEPGRAM_API_KEY in the environment"
)


@live
def test_live_preview_returns_a_wav():
    wav = asyncio.run(DeepgramSpeakClient(LIVE_KEY).preview(THALIA, "Hello, this is a live Deepgram preview."))
    assert wav[:4] == b"RIFF" and len(wav) > 10_000


@live
def test_live_streaming_tts_first_audio_is_fast():
    import time

    from service015.voice.deepgram_tts import AuraTTS

    async def run():
        import aiohttp

        async with aiohttp.ClientSession() as session:
            first, total = [], 0
            t = AuraTTS(model="aura-2-thalia-en", api_key=LIVE_KEY, http_session=session, on_first_audio=first.append)
            start = time.monotonic()
            async with t.synthesize("This is a streaming latency check.") as stream:
                async for ev in stream:
                    total += len(ev.frame.data)
            await t.aclose()
            return first[0] - start, total

    delay, total = asyncio.run(run())
    assert total > 0 and delay < 3.0
