"""Post-evidence fixes: session recreation error paths, ERROR latch until proven, Retry-After forms."""

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace

import pytest
from livekit.agents import APIStatusError

from service015 import identities_repo
from service015.runtime import presence as P
from service015.runtime import worker
from tests.test_final_fixes import ava


class Harness:
    def __init__(self, pres=None, start_failures=0, attempts=3):
        self.fails: list[tuple] = []
        self.shutdowns: list[str] = []
        self.starts = 0
        self.sleeps: list[float] = []
        self.tasks: list[asyncio.Task] = []
        self.start_failures = start_failures
        self.pres = pres

        async def start():
            self.starts += 1
            if self.starts <= self.start_failures:
                raise RuntimeError("livekit hiccup")

        async def sleep(d):
            self.sleeps.append(d)

        def fail(detail, message, terminal=False):
            self.fails.append((detail, message, terminal))
            if self.pres is not None:
                self.pres.fail(detail, message, terminal)

        self.driver = worker.RecoveryDriver(
            worker.SessionRecovery(attempts, 10.0),
            start=start,
            fail=fail,
            shutdown=self.shutdowns.append,
            spawn=lambda coro: self.tasks.append(asyncio.ensure_future(coro)),
            ended=lambda: False,
            sleep=sleep,
        )

    async def drain(self):
        await asyncio.gather(*self.tasks)


def test_start_failures_are_bounded_then_fail_job_and_shutdown():
    async def go():
        h = Harness(start_failures=99, attempts=3)
        h.driver.on_closed(P.LLM_UNAVAILABLE, "llm", RuntimeError("x"))
        await h.drain()
        return h

    h = asyncio.run(go())
    assert h.starts == 3  # max_attempts=3: every attempt (including the first from on_closed) was a failed start
    assert h.sleeps == [10.0, 20.0, 30.0]  # the same linear backoff applies to start failures
    assert h.shutdowns == ["session_recovery_exhausted"]
    detail, message, terminal = h.fails[-1]
    assert detail == P.LLM_UNAVAILABLE and terminal is True
    assert "session recreation failed" in message and "livekit hiccup" in message and "exhausted" in message


def test_start_failure_then_success_recovers_without_shutdown():
    async def go():
        h = Harness(start_failures=1, attempts=3)
        h.driver.on_closed(P.LLM_UNAVAILABLE, "llm", RuntimeError("x"))
        await h.drain()
        return h

    h = asyncio.run(go())
    assert h.starts == 2 and h.shutdowns == []
    assert h.sleeps == [10.0, 20.0]  # same linear backoff for the retry
    assert h.fails[-1][1].startswith("session recreated")


def test_initial_exhaustion_shuts_the_job_down():
    h = Harness(attempts=0)
    h.driver.on_closed(P.LLM_UNAVAILABLE, "llm", RuntimeError("x"))
    assert h.shutdowns == ["session_recovery_exhausted"] and h.starts == 0
    assert h.fails[-1][2] is True


def test_error_stays_latched_after_recreate_until_llm_proves(tmp_path):
    db = ava(tmp_path / "p.db")
    pres = P.PresenceController(db, "ava-001", pid=1)
    pres.claim()
    pres.set_humans(frozenset())

    async def go():
        h = Harness(pres=pres, attempts=3)
        h.driver.on_closed(P.LLM_RATE_LIMITED, "llm", RuntimeError("x"))
        await h.drain()

    asyncio.run(go())
    st = identities_repo.get_status(db, "ava-001")
    assert st.presence == "ERROR" and st.status_detail == P.LLM_RATE_LIMITED  # session is up, LLM not yet proven
    assert "recovering" in (st.last_error or "")
    pres.recovered(P.LLM_RATE_LIMITED)  # what on_agent_state does on the first successful reply
    assert identities_repo.get_status(db, "ava-001").presence == "READY"


@pytest.mark.parametrize(
    "value,expected",
    [("2", 2.0), ("2.5", 2.5), ("850ms", 0.85), ("1.2s", 1.2)],
)
def test_retry_after_header_forms(value, expected):
    exc = APIStatusError("x", status_code=429)
    exc.headers = {"retry-after": value}
    assert worker.retry_after_seconds(exc) == pytest.approx(expected)


def test_retry_after_http_date():
    exc = APIStatusError("x", status_code=429)
    exc.headers = {"retry-after": format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)}
    assert 25 <= worker.retry_after_seconds(exc) <= 31
    exc.headers = {"retry-after": "garbage"}
    assert worker.retry_after_seconds(exc) is None


@pytest.mark.parametrize(
    "msg,expected",
    [("Please try again in 850ms.", 0.85), ("try again in 1.2s", 1.2), ("try again in 1m2.5s.", 62.5), ("try again in 7s", 7.0)],
)
def test_retry_after_groq_message_forms(msg, expected):
    assert worker.retry_after_seconds(APIStatusError(msg, status_code=429)) == pytest.approx(expected)
