"""Self-echo suppression for streaming STT (Deepgram mode).

Deepgram transcribes everything the microphone carries, including the agent's own voice leaking back from the user's speakers.
Without a guard that echo arrives as interim/final transcripts while the agent is still speaking; the session reads it as the
user barging in, cuts the reply mid-sentence and then answers its own words. Moonshine never showed this because it only emits
finals after the VAD closes the utterance.

`SelfEchoGuard` remembers what the agent is saying (the LLM text of the last generations) and whether it is speaking.
`EchoFilteredSTT` wraps `deepgram.STT` and drops a transcript event whose words are (almost) all words of that text while the
agent is speaking, plus a short tail for late finals. Real user speech carries words the agent did not say, so it passes.
"""

from __future__ import annotations

import difflib
import logging
import re
import time
from collections import deque
from typing import Any, Callable

from livekit.agents import stt
from livekit.plugins import deepgram

log = logging.getLogger("service015.runtime")

_WORD_RE = re.compile(r"[a-z0-9']+")
_TRANSCRIPTS = (stt.SpeechEventType.INTERIM_TRANSCRIPT, stt.SpeechEventType.FINAL_TRANSCRIPT)


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


class SelfEchoGuard:
    """What the agent is saying right now, and the test "is this transcript just the agent hearing itself"."""

    def __init__(
        self,
        *,
        tail_s: float = 1.2,
        generations: int = 2,
        similarity: float = 0.8,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tail_s = tail_s
        self._similarity = similarity
        self._clock = clock
        self._texts: deque[list[str]] = deque([[]], maxlen=generations)
        self._speaking = False
        self._stopped_at: float | None = None

    def begin_generation(self) -> None:
        self._texts.append([])

    def add_text(self, text: str) -> None:
        if text:
            self._texts[-1].append(text)

    def on_agent_state(self, new_state: str) -> None:
        was_speaking = self._speaking
        self._speaking = new_state == "speaking"
        if was_speaking and not self._speaking:
            self._stopped_at = self._clock()

    @property
    def active(self) -> bool:
        if self._speaking:
            return True
        return self._stopped_at is not None and self._clock() - self._stopped_at <= self._tail_s

    def is_echo(self, transcript: str) -> bool:
        if not self.active:
            return False
        heard = _words(transcript)
        if not heard:
            return False
        vocab = sorted({w for chunks in self._texts for w in _words("".join(chunks))})
        if not vocab:
            return False
        known = set(vocab)
        matched = sum(1 for w in heard if w in known or difflib.get_close_matches(w, vocab, n=1, cutoff=self._similarity))
        if len(heard) <= 3:
            return matched == len(heard)
        return matched / len(heard) >= 0.75


class _FilteredChan:
    """Event channel proxy: transcript events that are the agent's own echo are dropped, everything else is forwarded."""

    def __init__(self, inner: Any, guard: SelfEchoGuard) -> None:
        self._inner = inner
        self._guard = guard

    def send_nowait(self, ev: stt.SpeechEvent) -> None:
        if ev.type in _TRANSCRIPTS and ev.alternatives and self._guard.is_echo(ev.alternatives[0].text):
            log.debug("dropped self-echo transcript %r", ev.alternatives[0].text)
            return
        self._inner.send_nowait(ev)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class EchoFilteredSTT(deepgram.STT):
    def __init__(self, *, echo_guard: SelfEchoGuard, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.echo_guard = echo_guard

    def stream(self, **kwargs: Any) -> deepgram.stt.SpeechStream:
        stream = super().stream(**kwargs)
        # The stream's task has not run yet (nothing awaited since construction), so every event goes through the proxy.
        stream._event_ch = _FilteredChan(stream._event_ch, self.echo_guard)
        return stream
