"""BM25 full-text search across the catalog.

The index uses field boosting via document repetition: each method's
``summary`` is repeated 4x, ``path`` and ``operation_id`` 3x each, ``section``
and ``tag`` 2x each, and ``description`` 1x. BM25Okapi doesn't support
explicit field weights, so repetition is the standard workaround and gives
us roughly the same effect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import snowballstemmer
from rank_bm25 import BM25Okapi

from wb_mcp.schema.catalog import Catalog
from wb_mcp.schema.extractor import Method

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_CAMEL_RE = re.compile(r"[A-Z][a-z]+|[A-Z]+(?=[A-Z]|$)|[a-z]+|\d+")
_RU_STEMMER = snowballstemmer.stemmer("russian")
_EN_STEMMER = snowballstemmer.stemmer("english")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")

_BOOST_SUMMARY = 4
_BOOST_PATH = 3
_BOOST_OPERATION_ID = 3
_BOOST_SECTION = 2
_BOOST_TAG = 2
_BOOST_DESCRIPTION = 1

_OP_ID_NOISE = frozenset(
    {
        "api", "ap",
        "v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10",
    }
)

_GET_KEYWORDS = frozenset(
    {
        "get", "list", "fetch", "info", "details",
        "search", "find", "summary", "stat", "analytics",
    }
)

_WRITE_KEYWORDS = frozenset(
    {
        "create", "add", "update", "delete", "remove",
        "cancel", "archive", "import", "upload", "set",
    }
)

_RUSSIAN_QUERY_EXPANSIONS: dict[str, list[str]] = {
    "товар": ["товары", "товар"],
    "заказ": ["заказы", "заказ", "отправление", "отправления"],
    "цена": ["цены", "цена"],
    "склад": ["склады", "склад"],
    "акция": ["акции", "акция"],
    "отчёт": ["отчёты", "отчёт"],
    "отчет": ["отчёты", "отчёт"],
    "возврат": ["возвраты", "возврат"],
    "остаток": ["остатки", "остаток"],
}


def _expand_russian_query(query: str) -> str:
    raw_tokens = query.split()
    if not raw_tokens:
        return query
    expanded: list[str] = list(raw_tokens)
    for token in raw_tokens:
        variants = _RUSSIAN_QUERY_EXPANSIONS.get(token.lower())
        if variants:
            expanded.extend(variants)
    return " ".join(expanded)


def _camel_tokens(text: str) -> list[str]:
    return [t.lower() for t in _CAMEL_RE.findall(text)]


@dataclass
class SearchResult:
    method: Method
    score: float


class SearchIndex:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self._methods: list[Method] = list(catalog.methods)
        documents = [self._document(m) for m in self._methods]
        self._bm25 = BM25Okapi(documents, b=0.3, k1=1.5)

    def search(
        self,
        query: str,
        *,
        section: str | None = None,
        api: str | None = None,
        category: str | None = None,
        limit: int = 10,
        include_deprecated: bool = False,
    ) -> list[SearchResult]:
        expanded_query = _expand_russian_query(query)
        tokens = _tokenize_query(expanded_query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        query_lower = query.lower().strip()
        query_plain = set(_tokenize(expanded_query))
        query_camel = set(_tokenize_with_camel(expanded_query))

        adjusted: list[tuple[int, float]] = []
        for i, s in enumerate(scores):
            m = self._methods[i]
            mult = 1.0

            if m.deprecated:
                mult *= 0.3

            summary_lower = m.summary.lower().strip()
            if summary_lower:
                if query_lower == summary_lower:
                    mult *= 8.0
                elif len(query_lower) >= 3 and query_lower in summary_lower:
                    ratio = len(query_lower) / len(summary_lower)
                    mult *= 1.0 + 4.0 * ratio

            summary_tokens_cached: set[str] = (
                set(_tokenize(m.summary)) if query_plain else set()
            )
            if query_plain and query_plain.issubset(summary_tokens_cached):
                mult *= 2.0

            op_lower = m.operation_id.lower()
            if query_camel:
                op_tokens = set(_tokenize_with_camel(m.operation_id)) - _OP_ID_NOISE
                if op_tokens and query_camel.issubset(op_tokens):
                    precision = len(query_camel) / len(op_tokens)
                    mult *= 1.0 + 6.0 * precision
                elif op_tokens:
                    overlap = query_camel & op_tokens
                    if len(overlap) >= 2 and len(overlap) >= len(query_camel) * 0.5:
                        mult *= 1.0 + 1.5 * (len(overlap) / len(op_tokens))

            query_concat = re.sub(r"\W+", "", query.lower())
            if len(query_concat) >= 5 and query_concat in op_lower:
                mult *= 2.5

            if "/" in query_lower and query_lower.strip("/") in m.path.lower().strip("/"):
                mult *= 4.0

            if m.safety == "read":
                mult *= 1.35

            op_camel = set(_camel_tokens(m.operation_id))
            if op_camel & _GET_KEYWORDS:
                mult *= 1.1
            if m.safety != "read" and op_camel & _WRITE_KEYWORDS:
                mult *= 0.85

            if m.safety == "destructive":
                mult *= 0.6

            if (
                m.safety == "read"
                and query_plain
                and query_plain.issubset(summary_tokens_cached)
                and 0 < len(summary_tokens_cached) <= 3
            ):
                mult *= 2.5

            if m.safety == "read" and query_plain:
                section_tokens = set(_tokenize(m.section))
                if query_plain.issubset(section_tokens):
                    mult *= 1.4

            if m.safety == "read" and query_plain and m.description:
                description_tokens = set(_tokenize(m.description))
                if query_plain.issubset(description_tokens):
                    mult *= 1.25

            adjusted.append((i, s * mult))
        ranked = sorted(adjusted, key=lambda x: -x[1])
        results: list[SearchResult] = []
        for idx, score in ranked:
            if score <= 0:
                break
            m = self._methods[idx]
            if not include_deprecated and m.deprecated:
                continue
            if section and section.lower() not in m.section.lower() and section.lower() not in m.tag.lower():
                continue
            if api and m.api != api:
                continue
            if category and not (m.category and m.category.lower() == category.lower()):
                continue
            results.append(SearchResult(method=m, score=float(score)))
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _document(m: Method) -> list[str]:
        out: list[str] = []
        out.extend(_tokenize(m.summary) * _BOOST_SUMMARY)
        out.extend(_tokenize_with_camel(m.path) * _BOOST_PATH)
        out.extend(_tokenize_with_camel(m.operation_id) * _BOOST_OPERATION_ID)
        out.extend(_tokenize(m.section) * _BOOST_SECTION)
        out.extend(_tokenize(m.tag) * _BOOST_TAG)
        out.extend(_tokenize(m.description) * _BOOST_DESCRIPTION)
        return out


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower()):
        if len(raw) <= 1:
            continue
        if _CYRILLIC_RE.search(raw):
            out.append(_RU_STEMMER.stemWord(raw))
        else:
            out.append(_EN_STEMMER.stemWord(raw))
    return out


def _tokenize_with_camel(text: str) -> list[str]:
    out: list[str] = []
    for chunk in _TOKEN_RE.findall(text):
        for word in _CAMEL_RE.findall(chunk):
            w = word.lower()
            if len(w) <= 1:
                continue
            if _CYRILLIC_RE.search(w):
                out.append(_RU_STEMMER.stemWord(w))
            else:
                out.append(_EN_STEMMER.stemWord(w))
    return out


def _tokenize_query(query: str) -> list[str]:
    plain = _tokenize(query)
    camel = _tokenize_with_camel(query)
    if not plain:
        return camel
    if not camel:
        return plain
    seen = set(plain)
    out = list(plain)
    for t in camel:
        if t not in seen:
            out.append(t)
            seen.add(t)
    return out