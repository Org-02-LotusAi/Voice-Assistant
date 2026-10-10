"""IdentityAgent.llm_node must not let an empty LLM completion silently end the turn (barge-in / ten-turn acceptance defect)."""

import asyncio

import pytest
from livekit.agents import Agent, llm

from service015.runtime import worker
from service015.runtime.latency import LatencyRecorder


def _chunk(text: str | None) -> llm.ChatChunk:
    return llm.ChatChunk(id="c", delta=llm.ChoiceDelta(role="assistant", content=text))


class _Script:
    """Stands in for Agent.default.llm_node: each call plays the next scripted list of chunks."""

    def __init__(self, calls):
        self.calls = list(calls)
        self.n = 0

    async def __call__(self, agent, chat_ctx, tools, model_settings):
        chunks = self.calls[self.n]
        self.n += 1
        for c in chunks:
            yield c


def _run(monkeypatch, calls):
    script = _Script(calls)
    monkeypatch.setattr(Agent.default, "llm_node", script)
    rec = LatencyRecorder(lambda: None, "ava-001", "s")
    agent = worker.IdentityAgent(instructions="x", recorder=rec)

    async def go():
        return [c async for c in agent.llm_node(llm.ChatContext.empty(), [], None)]

    return asyncio.run(go()), script, agent


def test_empty_completion_is_retried_and_the_reply_is_spoken(monkeypatch):
    out, script, agent = _run(monkeypatch, [[_chunk(""), _chunk(None)], [_chunk(""), _chunk("Our pricing is quoted per plan.")]])
    assert script.n == 2
    assert "".join(worker._chunk_text(c) for c in out) == "Our pricing is quoted per plan."
    assert agent.chunks_seen == 1


def test_normal_completion_is_not_repeated(monkeypatch):
    out, script, _ = _run(monkeypatch, [[_chunk("Hello there.")]])
    assert script.n == 1 and len(out) == 1


def test_persistently_empty_completion_stops_after_the_cap_and_never_invents_text(monkeypatch, caplog):
    out, script, agent = _run(monkeypatch, [[_chunk("")]] * worker.MAX_EMPTY_LLM_ATTEMPTS)
    assert script.n == worker.MAX_EMPTY_LLM_ATTEMPTS
    assert "".join(worker._chunk_text(c) for c in out) == ""
    assert agent.chunks_seen == 0
    assert any("no reply" in r.message for r in caplog.records)
