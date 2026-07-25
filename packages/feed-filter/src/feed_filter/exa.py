"""Exa neural-search client — the query gather's network layer.

The third gather kind. Where the article and forum paths poll *registered places*,
this one asks a *question*: the caller supplies a natural-language description and
Exa returns pages whose meaning matches it, so a page is reachable without knowing
the words it uses. That is the one thing a registered feed cannot do.

Deterministic and network-only, like ``fetch``/``feeds`` — no dedupe, no seen-store,
no vault. ``cli.cmd_query_new`` composes those around it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from feed_filter.config import EXA_ENDPOINT, SUMMARY_PREVIEW_CHARS, env_exa_key
from feed_filter.feeds import MAX_BODY_CHARS

# Exa's own snippet is the query gather's stand-in for feed metadata: it lets the
# judge drop an out-of-scope page without fetching it, the same short-circuit the
# article path gets from ``<description>``.
#
# The cap is not about cost — text for the first ten results rides on the request
# price, and raising ``maxCharacters`` tenfold leaves the reported cost unchanged
# (measured). It is about reach: a query entry has no ``body_cache`` behind it the
# way a feed entry does, so anything past the preview the CLI emits is bytes
# nobody can read. Text rather than an AI summary because a summary is a
# separately-billed content type and paraphrases besides, and the judge is
# assessing the page's own depth.
_CONTENTS = {"text": {"maxCharacters": SUMMARY_PREVIEW_CHARS}}


class ExaError(Exception):
    """An Exa request failed: transport, non-2xx, or an unparseable body.

    Raised per query so ``cmd_query_new`` can record the failure against that one
    query and still emit the others — a failed query records nothing seen, so the
    next run retries it (the never-lost bias the article gather also follows).
    """


@dataclass(frozen=True)
class QueryHit:
    """One Exa result. ``text`` is Exa's cached page text, empty when it has none."""

    url: str
    title: str
    text: str


@dataclass(frozen=True)
class QueryOutcome:
    """One query's network outcome. ``error`` set means ``hits`` is empty."""

    query: str
    hits: tuple[QueryHit, ...]
    cost_dollars: float
    error: str | None = None


def _clean(value: object) -> str:
    """Coerce an untrusted JSON scalar to a stripped, UTF-8-encodable string.

    ``json.loads`` accepts a ``\\udXXX`` escape and hands back a lone surrogate,
    which UTF-8 cannot encode. Left in, it raises far downstream — in
    ``canonical_url``'s query re-encoding, or in the CLI's ``json.dumps`` — and
    aborts the whole invocation over one hit, losing every query's results and the
    record of what was already billed. A truncating producer emits these routinely:
    slicing a UTF-16 string at a fixed length splits an astral character in half.
    Replacing here keeps the repair at the single untrusted-input boundary.
    """
    text = value.strip() if isinstance(value, str) else ""
    return text.encode("utf-8", "replace").decode("utf-8")


def _web_url(value: object) -> str:
    """The hit's URL if it is an absolute http(s) one, else ``""`` (caller skips).

    The same non-http(s) rejection ``canonical.resolve_link`` applies to link
    values scraped out of a document, for the same reason: a ``javascript:`` or
    ``file:`` URI would be handed onward to a fetch, written into a ``Feeds/``
    note, and recorded as a seen-store key. ``resolve_link`` itself does not fit
    here — it resolves a *relative* href against the document that carried it, and
    an Exa hit has no such document, so a non-absolute value is malformed data to
    drop rather than a relative link to resolve.

    Parsing is guarded because ``urlsplit`` raises on an unbalanced IPv6 bracket
    and ``SplitResult.port`` raises on a port that is out of range or not a number
    — and ``canonical_url`` reads that port downstream. Unguarded, one such string
    anywhere in one query's results would abort the whole invocation, losing the
    other queries' results and the report of what was already billed. Dropping the
    hit keeps a malformed payload costing only itself, which is what this gather
    promises.
    """
    url = _clean(value)
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 — raises on a malformed port, which is the check
    except ValueError:
        return ""
    return url if parts.scheme in ("http", "https") else ""


def usable_cost(value: float) -> float:
    """``value`` if it is a finite float, else ``0.0``.

    Exported because the per-query guard is not enough on its own: the CLI sums
    these across a run, and a sum can overflow to infinity even when every term was
    finite. An emitted ``Infinity`` is a bare non-JSON token, which would make the
    whole document unreadable to the skills the CLI exists to serve.
    """
    return value if math.isfinite(value) else 0.0


def _cost(payload: dict[str, Any]) -> float:
    """Exa's reported cost, or ``0.0`` when absent, non-numeric, or unusable.

    The same boundary discipline ``_clean`` applies to text. An integer too large
    for a float raises ``OverflowError``, which ``cli.main`` does not catch, so it
    would dump a traceback and lose every query's results; a non-finite float would
    reach stdout as a bare ``Infinity``/``NaN`` token, which is not JSON and would
    make the whole document unreadable to the skills the CLI exists to serve. A
    bool is an ``int`` in Python but is not a cost.
    """
    cost = payload.get("costDollars")
    total = cost.get("total") if isinstance(cost, dict) else None
    if not isinstance(total, (int, float)) or isinstance(total, bool):
        return 0.0
    try:
        value = float(total)
    except OverflowError:
        return 0.0
    return usable_cost(value)


def search(client: httpx.Client, query: str, *, num_results: int) -> QueryOutcome:
    """Run one neural search; return its hits, or an outcome carrying the error.

    Exa's response is untrusted input — every field is coerced, and a hit whose URL
    is not an absolute http(s) one is dropped rather than trusted to be
    well-formed. Page text is capped at ``MAX_BODY_CHARS`` on top of the API-side
    ``maxCharacters``, the same guard the feed path puts on a pathological body.

    ``cost_dollars`` is a floor, not a total: a request Exa billed but answered
    with an unparseable body reports 0, since the figure is only ever read out of
    a well-formed response.
    """
    key = env_exa_key()
    if not key:
        raise ExaError("EXA_API_KEY is unset — add it to the workspace .env")
    body = {
        "query": query,
        "numResults": num_results,
        "type": "neural",
        "contents": _CONTENTS,
    }
    try:
        resp = client.post(
            EXA_ENDPOINT,
            json=body,
            headers={"x-api-key": key, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:  # ValueError covers a bad JSON body
        return QueryOutcome(
            query=query, hits=(), cost_dollars=0.0, error=f"{type(exc).__name__}: {exc}"
        )
    if not isinstance(payload, dict):
        return QueryOutcome(
            query=query, hits=(), cost_dollars=0.0, error="malformed response: not an object"
        )
    raw = payload.get("results")
    hits = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        url = _web_url(item.get("url"))
        if not url:
            continue
        hits.append(
            QueryHit(
                url=url,
                title=_clean(item.get("title")),
                text=_clean(item.get("text"))[:MAX_BODY_CHARS],
            )
        )
    return QueryOutcome(query=query, hits=tuple(hits), cost_dollars=_cost(payload))
