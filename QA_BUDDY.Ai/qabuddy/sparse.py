"""BM25 sparse vectors for Qdrant's keyword side of hybrid search.

Qdrant stores the term frequencies (with BM25's saturation and length
normalization applied here) and multiplies in the inverse document frequency
itself at query time (`Modifier.IDF`), so IDF always reflects the current
collection.

The tokenizer is written for QA data, where exact identifiers matter most:
`WING-LOGIN-TC-001`, `VWO-1234`, `DriverManager`, `loginToVWO`. Such tokens
are indexed whole *and* split into their parts, so both "WING-LOGIN-TC-001"
and "login tc 001" match. Plain words are lowercased and stemmed.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

import snowballstemmer

K1 = 1.2
B = 0.75
AVG_DOC_TOKENS = 220  # typical chunk length in tokens of this tokenizer

_TOKEN = re.compile(r"[A-Za-z0-9_]+(?:[-./:#][A-Za-z0-9_]+)*")
_PARTS = re.compile(r"[-./:#_]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z]+|[A-Z]+|\d+")

STOPWORDS = frozenset(
    """a about above after again against all am an and any are as at be because been before being below
    between both but by can could did do does doing down during each few for from further had has have
    having he her here hers herself him himself his how i if in into is it its itself just me more most my
    myself no nor not now of off on once only or other our ours ourselves out over own same she should so
    some such than that the their theirs them themselves then there these they this those through to too
    under until up very was we were what when where which while who whom why will with would you your
    yours yourself yourselves also via per etc e.g i.e please give show tell list find get
    public private protected static final void return new import package const let var this await async
    function export default extends implements class interface true false null undefined string""".split()
)

_stemmer = snowballstemmer.stemmer("english")


@dataclass(frozen=True)
class SparseVector:
    indices: list[int]
    values: list[float]


@lru_cache(maxsize=200_000)
def _stem(word: str) -> str:
    return _stemmer.stemWord(word)


def _index(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest(), "big")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN.finditer(text):
        raw = match.group(0)
        pieces = [p for part in _PARTS.split(raw) for p in _CAMEL.findall(part)]
        if len(pieces) > 1:
            whole = raw.lower()
            if len(whole) <= 64:
                tokens.append(whole)  # the identifier as written
            compact = re.sub(r"[-./:#_]", "", whole)
            if compact != whole and len(compact) <= 64:
                tokens.append(compact)
        for piece in pieces or [raw]:
            word = piece.lower()
            if word in STOPWORDS or (len(word) < 2 and not word.isdigit()):
                continue
            tokens.append(_stem(word) if word.isalpha() else word)
    return tokens


def encode_document(text: str) -> SparseVector:
    counts = Counter(tokenize(text))
    length = sum(counts.values()) or 1
    norm = K1 * (1 - B + B * length / AVG_DOC_TOKENS)
    weights: dict[int, float] = {}
    for token, tf in counts.items():
        index = _index(token)
        weights[index] = weights.get(index, 0.0) + tf * (K1 + 1) / (tf + norm)
    return SparseVector(list(weights), list(weights.values()))


def encode_query(text: str, extra_terms: list[str] | None = None) -> SparseVector:
    """Each distinct query term weighs 1; glossary expansions weigh 0.5."""
    weights: dict[int, float] = {}
    for token in tokenize(text):
        weights[_index(token)] = 1.0
    for term in extra_terms or []:
        for token in tokenize(term):
            weights.setdefault(_index(token), 0.5)
    return SparseVector(list(weights), list(weights.values()))
