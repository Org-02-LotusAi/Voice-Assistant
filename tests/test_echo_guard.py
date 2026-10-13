"""Deepgram mode: the agent's own voice leaking into the mic must not cut its reply (task D2 regression tests).

Live defect: Deepgram transcribed the agent's speech coming back through the user's speakers; the transcripts interrupted the
reply mid-sentence ("The water" ... silence) and the echo was then answered as a user turn. Moonshine (finals only) never did.
"""

import asyncio
from types import SimpleNamespace

from livekit.agents import Agent, llm, stt
from livekit.plugins import deepgram

from service015.config import Settings
from service015.runtime import worker
from service015.runtime.latency import LatencyRecorder
from service015.runtime.procs import allowed_env
from service015.stt.echo_guard import EchoFilteredSTT, SelfEchoGuard, _FilteredChan

KEY = "dg-test-key-0123456789abcdef"
REPLY = "Typically we work with teams that manage high volumes of client interactions and need better visibility."


class Clock:
    t = 100.0

    def __call__(self) -> float:
        return self.t


def guard(clock=None, **kw) -> SelfEchoGuard:
    return SelfEchoGuard(clock=clock or Clock(), **kw)


def speaking_guard(clock=None, text=REPLY, **kw) -> SelfEchoGuard:
    g = guard(clock, **kw)
    g.begin_generation()
    g.add_text(text)
    g.on_agent_state("speaking")
    return g


# ---------- the echo test ----------


def test_no_echo_when_the_agent_is_silent():
    g = guard()
    g.begin_generation()
    g.add_text(REPLY)
    assert not g.is_echo("Typically we work with teams")  # the agent has not started speaking


def test_agent_words_heard_while_speaking_are_echo():
    g = speaking_guard()
    assert g.is_echo("Typically,")
    assert g.is_echo("teams that manage high volumes of client interactions,")
    assert g.is_echo("Typically we work with teams that manage high vol")  # truncated word still matches by similarity


def test_speech_with_words_the_agent_did_not_say_is_not_echo():
    g = speaking_guard()
    assert not g.is_echo("Stop, I have a question")
    assert not g.is_echo("What is the price?")
    assert not g.is_echo("")


def test_long_transcript_with_a_new_clause_is_not_echo():
    g = speaking_guard()
    assert not g.is_echo("teams that manage high volumes but actually my budget is small")


def test_mis_hearing_tolerated_on_long_echo():
    g = speaking_guard()
    assert g.is_echo("Typically we worked with team that managed high volume of client interaction and need better visibility")


def test_late_final_within_the_tail_is_still_echo_then_the_guard_releases():
    clock = Clock()
    g = speaking_guard(clock)
    g.on_agent_state("listening")
    clock.t += 0.5
    assert g.is_echo("better visibility")
    clock.t += 1.5  # past the tail: this is the user answering
    assert not g.is_echo("better visibility")


def test_vocabulary_covers_the_last_two_generations_only():
    g = speaking_guard(text="Alpha bravo charlie.")
    g.begin_generation()
    g.add_text("Delta echo foxtrot.")
    assert g.is_echo("alpha bravo")
    g.begin_generation()
    g.add_text("Golf hotel india.")
    assert not g.is_echo("alpha bravo") and g.is_echo("golf hotel")


# ---------- the STT wrapper ----------


def ev(kind, text=""):
    alts = [stt.SpeechData(language="en", text=text)] if text else []
    return stt.SpeechEvent(type=kind, alternatives=alts)


class Sink:
    def __init__(self):
        self.sent, self.closed = [], False

    def send_nowait(self, e):
        self.sent.append(e)

    def close(self):
        self.closed = True


def test_filtered_channel_drops_only_echo_transcripts():
    g = speaking_guard()
    sink = Sink()
    ch = _FilteredChan(sink, g)
    kinds = stt.SpeechEventType
    echo_interim = ev(kinds.INTERIM_TRANSCRIPT, "teams that manage")
    echo_final = ev(kinds.FINAL_TRANSCRIPT, "teams that manage high volumes.")
    user_final = ev(kinds.FINAL_TRANSCRIPT, "Actually I have a question about pricing")
    start, end = ev(kinds.START_OF_SPEECH), ev(kinds.END_OF_SPEECH)
    for e in (start, echo_interim, echo_final, user_final, end):
        ch.send_nowait(e)
    assert sink.sent == [start, user_final, end]
    ch.close()
    assert sink.closed  # everything else (close, closed, ...) reaches the real channel


def test_echo_filtered_stt_routes_the_stream_events_through_the_guard(monkeypatch):
    sink = Sink()
    fake_stream = SimpleNamespace(_event_ch=sink)
    monkeypatch.setattr(deepgram.STT, "stream", lambda self, **kw: fake_stream)
    s = EchoFilteredSTT(echo_guard=speaking_guard(), api_key=KEY, model="nova-3", language="en")
    out = s.stream()
    assert out is fake_stream and isinstance(out._event_ch, _FilteredChan)
    out._event_ch.send_nowait(ev(stt.SpeechEventType.FINAL_TRANSCRIPT, "client interactions"))
    assert sink.sent == []


# ---------- worker wiring ----------


def settings(**kw) -> Settings:
    return Settings(SERVICE015_API_KEYS="adm:admin", _env_file=None, **kw)


def resolved(barge_in=True):
    return SimpleNamespace(
        conversation=SimpleNamespace(barge_in=barge_in), voice=SimpleNamespace(profile_id="vp"), self_=SimpleNamespace(language="en-US")
    )


def test_deepgram_barge_in_needs_words_and_local_mode_is_unchanged():
    dg = worker.interruption_options(settings(SERVICE015_STT_PROVIDER="deepgram", DEEPGRAM_API_KEY=KEY), resolved())
    assert dg == {"enabled": True, "mode": "vad", "min_duration": 0.3, "min_words": 2}
    tuned = settings(SERVICE015_STT_PROVIDER="deepgram", DEEPGRAM_API_KEY=KEY, SERVICE015_DEEPGRAM_INTERRUPT_MIN_WORDS="0")
    assert "min_words" not in worker.interruption_options(tuned, resolved())  # 0 = VAD only
    local = worker.interruption_options(settings(), resolved(barge_in=False))
    assert local == {"enabled": False, "mode": "vad", "min_duration": 0.3}
    local_with_setting = settings(SERVICE015_DEEPGRAM_INTERRUPT_MIN_WORDS="3")
    assert "min_words" not in worker.interruption_options(local_with_setting, resolved())


def test_min_words_setting_reaches_the_worker_env():
    from service015.runtime.supervisor import worker_env

    env = worker_env(settings(SERVICE015_STT_PROVIDER="deepgram", DEEPGRAM_API_KEY=KEY, SERVICE015_DEEPGRAM_INTERRUPT_MIN_WORDS="3"))
    assert env["SERVICE015_DEEPGRAM_INTERRUPT_MIN_WORDS"] == "3"


def test_build_stt_uses_the_echo_filter_only_with_a_guard():
    s = settings(SERVICE015_STT_PROVIDER="deepgram", DEEPGRAM_API_KEY=KEY)
    with_guard = worker.build_stt(s, resolved(), None, SelfEchoGuard())
    assert isinstance(with_guard, EchoFilteredSTT) and with_guard._opts.endpointing_ms == worker.DEEPGRAM_ENDPOINTING_MS
    assert type(worker.build_stt(s, resolved(), None)) is deepgram.STT


def test_identity_agent_feeds_the_guard_with_the_spoken_text(monkeypatch):
    async def fake_default(agent, chat_ctx, tools, model_settings):
        for t in ("Hello ", "there."):
            yield llm.ChatChunk(id="c", delta=llm.ChoiceDelta(role="assistant", content=t))

    monkeypatch.setattr(Agent.default, "llm_node", fake_default)
    g = guard()
    agent = worker.IdentityAgent(instructions="x", recorder=LatencyRecorder(lambda: None, "i", "s"), echo_guard=g)

    async def go():
        return [c async for c in agent.llm_node(llm.ChatContext.empty(), [], None)]

    asyncio.run(go())
    g.on_agent_state("speaking")
    assert g.is_echo("hello there")


def test_runtime_log_level_reaches_the_worker():
    assert allowed_env({"SERVICE015_RUNTIME_LOG": "DEBUG", "SERVICE015_API_KEYS": "x"}) == {"SERVICE015_RUNTIME_LOG": "DEBUG"}
