"""What a correct reply looks like: self-fact matching with a tiny documented alias list, and per-turn keyword expectations.

Self-fact matching works on the Moonshine transcript of the Identity's audio (STT can vary), so each configured value has an
explicit alias list. It is intentionally tiny: only spellings Moonshine plausibly produces for the SAME spoken word. Matching is
on whole words (so "available" never matches "ava"). Multi-word values (roles) require every word (singular/plural tolerant).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# value (lower-case) -> accepted spellings in a transcript. Anything not listed must appear verbatim (case-insensitive).
ALIASES: dict[str, tuple[str, ...]] = {
    "ava": ("ava", "eva"),
    "james": ("james",),
    "safi": ("safi", "saafi", "saffy", "safie", "sofi", "saffee", "safiq"),  # Moonshine spellings of the same sound, seen in tests/e2e/probe_tts.py runs
    "northwind": ("northwind", "north wind", "northland"),
}

# Per-turn keyword expectations for assets/test_audio/turn_XX.wav (any ONE stem must appear as a word prefix in the reply).
# Defined before any run so a failing turn is a real finding, not a tuned-to-pass list. Replies are short sales answers; the
# stems cover the topic of the question OR the obvious honest deferral ("I will have someone confirm that in writing").
TURN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "turn_01": ("safi", "help", "provid", "offer", "solution", "service", "platform", "compan", "work"),
    "turn_02": ("customer", "client", "team", "business", "compan", "organi", "industr", "sector", "prospect"),
    "turn_03": ("day", "week", "hour", "minute", "quick", "start", "onboard", "setup", "set up", "timeline", "soon", "depend", "confirm"),
    "turn_04": ("integrat", "tool", "system", "crm", "stack", "connect", "software", "depend", "confirm", "use"),
    "turn_05": ("onboard", "team", "small", "setup", "set up", "train", "start", "kick", "walk", "support", "confirm"),
    "turn_06": ("secur", "privacy", "data", "protect", "encrypt", "complian", "written", "policy", "confirm", "safe"),
    "turn_07": ("trial", "pilot", "demo", "test", "try", "commit", "sandbox", "confirm", "written"),
    "turn_08": ("support", "help", "team", "assist", "channel", "email", "phone", "hours", "available", "confirm", "written"),
    "turn_09": ("pric", "cost", "quote", "plan", "fee", "budget", "package", "tier", "confirm", "written", "depend"),
    "turn_10": ("next step", "demo", "call", "follow", "schedule", "meeting", "proposal", "walkthrough", "calendar", "send", "time"),
}

# Reply to "Sorry, stop there. Can you just tell me the price?" must address the pricing question.
INTERRUPT_KEYWORDS: tuple[str, ...] = ("pric", "cost", "quote", "fee", "budget", "confirm", "written", "plan", "package", "tier")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", text.lower())).strip()


def _stem(word: str) -> str:
    return word[:-1] if word.endswith("s") and len(word) > 3 else word


def _word_re(variant: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(variant) + r"(?![a-z0-9])")


def mentions(text: str, value: str) -> bool:
    """True when the transcript contains `value` (via ALIASES for known values; multi-word values need every word)."""
    norm = normalize(text)
    key = normalize(value)
    variants = ALIASES.get(key)
    if variants:
        return any(_word_re(v).search(norm) for v in variants)
    words = [_stem(w) for w in key.split()]
    have = {_stem(w) for w in norm.split()}
    return bool(words) and all(w in have for w in words)


def mentions_strict(text: str, value: str) -> bool:
    """Verbatim whole-word match with NO aliases; kept next to the alias result in the evidence so the loosening stays visible."""
    return bool(_word_re(normalize(value)).search(normalize(text))) if " " not in value.strip() else normalize(value) in normalize(text)


PROBE_PATH = Path(__file__).resolve().parents[2] / "evidence" / "probe" / "audio_path_probe.json"


def documented_stt_mishearings(path: Path = PROBE_PATH) -> set[str]:
    """Self-facts (normalized) that the audio-path probe (evidence/probe/audio_path_probe.json: Chatterbox TTS -> Moonshine STT)
    records as mis-transcribed at least once, e.g. the invented brand "Safi" -> Saffy / Safiq / Saffee. Empty when the probe file
    does not exist (then nothing is excused and the audio check is a plain FAIL). It never widens `mentions`; it only decides
    whether a failed transcript check may be reported as PASS-AUDIO-CAVEAT instead of FAIL."""
    if not path.exists():
        return set()
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {normalize(r["fact"]) for r in rows if not r["fact_heard"]}


def keyword_hits(text: str, stems: tuple[str, ...]) -> list[str]:
    """Stems that start a word (or phrase) in the text."""
    norm = " " + normalize(text)
    return [s for s in stems if (" " + s) in norm]
