"""Sentence / clause splitting that bounds time-to-first-audio and the worst-case wait behind an in-flight chunk.

Chatterbox's token loop has no cancel hook, so an in-flight chunk cannot be aborted. Chunk length is therefore the
only lever on barge-in latency: the first chunk is ~FIRST_TARGET chars, later chunks are at most MAX_CHUNK chars.
Pure Python, no third-party imports.
"""
from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"')\]]*\s+|\n+")
_CLAUSE_END = re.compile(r"(?<=[,;:—–])\s+|\s+-\s+")

FIRST_CHUNK_MIN = 14  # chars: a first piece shorter than this is merged with what follows
FIRST_TARGET = 40  # a first sentence longer than this is cut: at a clause break <= FIRST_MAX, else at a word boundary
FIRST_MAX = 45
MAX_CHUNK = 120  # chars: no later chunk is longer (bounds the uninterruptible in-flight wait)
MERGE_BELOW = 10  # later short pieces are glued to a neighbour


def _split_sentences(text: str) -> list[str]:
    return [p.strip() for p in _SENTENCE_END.split(text) if p and p.strip()]


def _cut_first(sentence: str) -> tuple[str, str]:
    """Cut the opening sentence to ~FIRST_TARGET chars. Returns (head, rest); rest may be empty."""
    if len(sentence) <= FIRST_TARGET:
        return sentence, ""
    parts = [c for c in _CLAUSE_END.split(sentence) if c]
    head = ""
    for i, clause in enumerate(parts):
        head = f"{head} {clause}".strip()
        if len(head) > FIRST_MAX:
            break
        if len(head) >= FIRST_CHUNK_MIN:
            return head, " ".join(parts[i + 1:]).strip()
    # No usable clause break: cut at a word boundary near the target.
    idx = sentence.rfind(" ", 0, FIRST_TARGET + 1)
    if idx < FIRST_CHUNK_MIN:
        idx = sentence.find(" ", FIRST_CHUNK_MIN)
    if idx == -1:
        return sentence, ""
    return sentence[:idx].strip(), sentence[idx:].strip()


def _hard_wrap(clause: str) -> list[str]:
    out, cur = [], ""
    for w in clause.split():
        if cur and len(cur) + 1 + len(w) > MAX_CHUNK:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


def _split_long(piece: str) -> list[str]:
    """Break a piece longer than MAX_CHUNK at clause breaks, then at spaces."""
    if len(piece) <= MAX_CHUNK:
        return [piece]
    out: list[str] = []
    cur = ""
    for clause in (c for c in _CLAUSE_END.split(piece) if c):
        for group in _hard_wrap(clause):
            if cur and len(cur) + 1 + len(group) > MAX_CHUNK:
                out.append(cur)
                cur = group
            else:
                cur = f"{cur} {group}".strip()
    if cur:
        out.append(cur)
    return out


def split_for_streaming(text: str) -> list[str]:
    """Return chunks in speaking order. The first is ~40 chars; the rest are sentences of at most 120 chars."""
    sentences = _split_sentences(" ".join(text.split()) if "\n" not in text else "\n".join(" ".join(l.split()) for l in text.splitlines()))
    if not sentences:
        return []
    head, rest = _cut_first(sentences[0])
    sentences[0:1] = [head] + ([rest] if rest else [])
    # Merge tiny pieces forward so we never synthesize 2-word chunks (they sound clipped).
    merged: list[str] = []
    carry = ""
    for idx, s in enumerate(sentences):
        s = f"{carry} {s}".strip()
        carry = ""
        floor = FIRST_CHUNK_MIN if not merged else MERGE_BELOW
        if len(s) < floor and idx < len(sentences) - 1:
            carry = s
            continue
        merged.append(s)
    if carry:
        merged.append(carry)
    # A short final piece joins the previous one (never the first chunk, which must stay short).
    if len(merged) > 2 and len(merged[-1]) < MERGE_BELOW and len(merged[-2]) + 1 + len(merged[-1]) <= MAX_CHUNK:
        last = merged.pop()
        merged[-1] = f"{merged[-1]} {last}"
    chunks: list[str] = []
    for m in merged:
        chunks.extend(_split_long(m))
    return chunks
