"""Small in-repo Okapi BM25 ranker (no dependencies)."""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a an and are as at be by for from how i in is it its of on or that the this to was what when where which who why will with you your do does did can".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS]


class BM25:
    """Score a fixed corpus of documents against queries."""

    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._tf = [Counter(tokenize(d)) for d in documents]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg = (sum(self._len) / len(self._len)) if self._len else 0.0
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = len(documents)
        self._idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: str) -> list[float]:
        terms = set(tokenize(query))
        out: list[float] = []
        for tf, dl in zip(self._tf, self._len):
            s = 0.0
            for t in terms:
                f = tf.get(t)
                if not f:
                    continue
                norm = f + self.k1 * (1 - self.b + self.b * (dl / self._avg if self._avg else 0.0))
                s += self._idf[t] * f * (self.k1 + 1) / norm
            out.append(s)
        return out
