"""Sentence / clause splitting for low first-chunk latency. Pure Python, no third-party imports."""
from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"')\]]*\s+|\n+")
_CLAUSE_END = re.compile(r"(?<=[,;:—–])\s+|\s+-\s+")

FIRST_CHUNK_MIN = 14  # chars: a first piece shorter than this is merged with what follows
FIRST_CHUNK_TARGET = 60  # a first sentence longer than this is cut at its first natural clause break
MAX_CHUNK = 220  # chars: Chatterbox gets unstable on long outputs, so no chunk exceeds this
MERGE_BELOW = 10  # later sentences shorter than this are glued to the next one


def _split_sentences(text: str) -> list[str]:
    return [p.strip() for p in _SENTENCE_END.split(text) if p and p.strip()]


def _split_long(piece: str) -> list[str]:
    """Break a piece longer than MAX_CHUNK at clause breaks, then at spaces."""
    if len(piece) <= MAX_CHUNK:
        return [piece]
    out: list[str] = []
    cur = ""
    for clause in (c for c in _CLAUSE_END.split(piece) if c):
        for word_group in _hard_wrap(clause):
            if cur and len(cur) + 1 + len(word_group) > MAX_CHUNK:
                out.append(cur)
                cur = word_group
            else:
                cur = f"{cur} {word_group}".strip()
    if cur:
        out.append(cur)
    return out


def _hard_wrap(clause: str) -> list[str]:
    if len(clause) <= MAX_CHUNK:
        return [clause]
    words, out, cur = clause.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > MAX_CHUNK:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


def split_for_streaming(text: str) -> list[str]:
    """Return chunks in speaking order. The first chunk is as short as a natural clause allows."""
    text = " ".join(text.split()) if "\n" not in text else text.strip()
    sentences = _split_sentences(text)
    if not sentences:
        return []
    # Cut the first sentence at its first clause break when it is long.
    first = sentences[0]
    if len(first) > FIRST_CHUNK_TARGET:
        head = ""
        parts = [c for c in _CLAUSE_END.split(first) if c]
        for i, clause in enumerate(parts):
            head = f"{head} {clause}".strip()
            if len(head) >= FIRST_CHUNK_MIN:
                rest = " ".join(parts[i + 1:]).strip()
                sentences[0:1] = [head] + ([rest] if rest else [])
                break
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
    chunks: list[str] = []
    for m in merged:
        chunks.extend(_split_long(m))
    return chunks
