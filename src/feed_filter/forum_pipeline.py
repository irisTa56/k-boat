"""Forum pipeline — admission union and Rule-B candidate assembly (FRM-001 – FRM-007).

This module is the deterministic orchestration layer that turns due forum topics
into judge-ready candidates, mirroring ``pipeline.gather_new``'s read-only,
error-absorbing shape (FRM-PAT-001).

Two public entry points:

``admit_from_feeds(conn, site, *, client, now)`` — **Rule-A path (TASK-016)**:
    Fetches the three RSS feeds (latest + daily top + weekly top), builds the
    admission union deduped by topic id, calls ``forum_store.admit_topic`` for
    each (idempotent INSERT-OR-IGNORE), and emits a ``RuleACandidate`` for every
    newly-admitted topic whose ``op_interest_kept`` is still ``NULL`` (FRM-002).
    Feed-level ``FetchError`` is absorbed into ``error``; surviving feeds are
    still processed (FRM-CON-005).

``gather_forum(conn, site, *, client, now)`` — **Rule-B path (TASK-017/018)**:
    For each due topic (``forum_store.due_topics``), fetches its JSON, evaluates
    qualifying posts against the effective like threshold, and emits a
    ``RuleBCandidate`` carrying the trigger posts and threshold.  Also returns a
    **finalize worklist** (``GatherForumResult.polled_topics``) — one
    ``PolledTopic`` per due topic whose JSON was successfully fetched and parsed,
    including short-circuited topics and topics with no qualifying posts (but NOT
    topics whose fetch raised ``FetchError``).  This worklist is the seam the CLI
    needs to call ``finalize_poll`` after candidates are dispositioned, without
    ever calling ``finalize_poll`` for a topic whose fetch failed (FRM-CON-005 /
    FRM-007 — see Phase 5 review note: topics whose poll records are not advanced
    would re-poll every run, growing the watch set unboundedly).  Performs **no
    writes** to the DB (FRM-CON-005 / FRM-PAT-001); the record/finalize path
    (Phase 5 CLI) writes ``forum_post_seen`` and advances ``completed_polls``
    after all posts are dispositioned.  A topic-level ``FetchError`` is absorbed
    into ``error``; remaining topics continue (FRM-CON-005).

Design constraints honoured here:
- FRM-CON-001: only forum-adapter tables and code paths are touched.
- FRM-CON-002: no new runtime dependency — httpx + feedparser already present.
- FRM-CON-003: synchronous throughout; no asyncio.
- FRM-CON-004: fetch-minimization short-circuit documented inline.
- FRM-CON-005: never-lost over never-duplicated; error absorbing; no writes in gather.
- FRM-007: ``polled_topics`` worklist enables offset-only retirement without
  advancing polls for topics whose JSON fetch failed.
- FRM-GUD-001: all HTTP calls through ``fetch.fetch`` / ``FetchError``.
- FRM-GUD-002: ``parse_feed(sort=False)`` keeps top-feed rank order.
- FRM-GUD-003: ``canonical.canonical_url`` for topic URLs.
- FRM-GUD-004: forum-table SQL lives in ``forum_store``; this module calls its API.
- FRM-PAT-002: ``now`` and ``client`` are injected seams for unit testing.
- FRM-PAT-003: FRM-* ids cited in docstrings and comments throughout.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import httpx

from feed_filter import forum_store
from feed_filter.canonical import canonical_url
from feed_filter.config import (
    DEFAULT_DAILY_WATCH_COUNT,
    DEFAULT_INTEREST_LIKE_THRESHOLD,
    DEFAULT_LIKE_THRESHOLD,
    DEFAULT_POLL_OFFSETS_DAYS,
    DEFAULT_WEEKLY_WATCH_COUNT,
)
from feed_filter.discourse import (
    latest_feed_url,
    parse_topic,
    top_feed_url,
    topic_id_from_url,
    topic_json_url,
)
from feed_filter.feeds import parse_feed
from feed_filter.fetch import FetchError, fetch
from feed_filter.sites import SiteConfig

# ---------------------------------------------------------------------------
# Candidate dataclasses (TASK-017)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleACandidate:
    """A topic surfaced by the RSS discovery feeds for Rule-A OP judgment (FRM-002).

    The OP text comes from the RSS entry's ``summary`` (the feed description).
    No JSON fetch is needed at this stage (FRM-002); the CLI skill decides
    whether to ``WebFetch`` the topic page when the summary is too thin.
    """

    topic_id: int
    topic_url: str
    title: str
    op_text: str | None  # RSS entry summary; None for entries with no description


@dataclass(frozen=True)
class TriggerPost:
    """A single post that crossed the effective like threshold (FRM-003 / FRM-004).

    Carried inside ``RuleBCandidate``; the CLI skill judges each post
    independently and records them via ``forum-remind`` / ``forum-mark-seen``.
    """

    post_id: int
    post_number: int
    like_count: int
    text: str


@dataclass(frozen=True)
class RuleBCandidate:
    """A due topic with at least one qualifying, unseen post (FRM-003 / FRM-006).

    ``effective_threshold`` is the resolved like bar (interest or default),
    carried for the skill to surface in its judgment prompt.
    """

    topic_id: int
    topic_url: str
    title: str
    trigger_posts: list[TriggerPost] = field(default_factory=list)
    effective_threshold: int = DEFAULT_LIKE_THRESHOLD


@dataclass(frozen=True)
class AdmitResult:
    """Outcome of one ``admit_from_feeds`` call (TASK-016).

    ``candidates`` are Rule-A topics whose OP has not yet been judged.
    ``error`` is a combined message from any feed-level ``FetchError``(s),
    or ``None`` if all three feeds fetched successfully.
    ``fetch_count`` is the number of Discourse HTTP requests this call attempted
    (one per RSS feed, so at most three) — counted whether or not each succeeded,
    so the CLI can surface a per-run Discourse-call total for politeness
    observability (see ``cmd_forum_new``).  Default 0 keeps test fakes terse.
    """

    candidates: list[RuleACandidate]
    error: str | None
    fetch_count: int = 0


@dataclass(frozen=True)
class PolledTopic:
    """A due topic whose JSON was successfully fetched and parsed (FRM-007).

    Carried in ``GatherForumResult.polled_topics``; this is the **finalize
    worklist** the Phase 5 CLI uses to call ``forum_store.finalize_poll`` after
    all of a topic's candidates are dispositioned (FRM-CON-005).  Topics whose
    fetch raised ``FetchError`` are excluded — their polls must not be advanced
    (they will re-poll next run).  Short-circuited topics and topics with no
    qualifying posts ARE included: they still need ``finalize_poll`` so
    ``completed_polls`` advances and they eventually retire (FRM-007).
    """

    topic_id: int
    like_count: int  # freshly parsed topic.like_count, stored by finalize_poll (FRM-CON-004)


@dataclass(frozen=True)
class GatherForumResult:
    """Outcome of one ``gather_forum`` call (TASK-017/018).

    ``candidates`` are Rule-B topics with qualifying unseen posts.
    ``polled_topics`` is the finalize worklist — one ``PolledTopic`` per due
    topic whose JSON was successfully fetched and parsed (FRM-007 / FRM-CON-005).
    ``error`` is a combined message from any topic-level ``FetchError``(s),
    or ``None`` if every due topic fetched successfully.
    ``fetch_count`` is the number of Discourse topic-JSON requests this call
    attempted (one per due topic, counted whether or not each succeeded), so the
    CLI can report a per-run Discourse-call total.  Default 0 keeps fakes terse.
    """

    candidates: list[RuleBCandidate]
    polled_topics: list[PolledTopic]
    error: str | None
    fetch_count: int = 0


# ---------------------------------------------------------------------------
# Rule-A: admission union + candidate emission (TASK-016)
# ---------------------------------------------------------------------------


def admit_from_feeds(
    conn: sqlite3.Connection,
    site: SiteConfig,
    *,
    client: httpx.Client,
    now: int,
) -> AdmitResult:
    """Fetch the three discovery feeds, admit topics, and emit Rule-A candidates.

    Fetches ``latest.rss``, ``top.rss?period=daily``, and
    ``top.rss?period=weekly`` from ``site.forum_url`` (FRM-001).  Builds the
    admission union deduped by topic id — a topic surfaced by multiple feeds is
    admitted once; the first entry seen for a given topic id supplies its OP
    text (FRM-002).

    Calls ``forum_store.admit_topic`` for each topic in the union; this is the
    **only** write in the ``forum-new`` path (FRM-CON-005).  A topic is admitted
    ``poll_eligible`` iff it was surfaced by a top feed (daily ∪ weekly): only
    those are JSON-polled for Rule B, so a ``latest.rss``-only topic is judged
    once under Rule A but never enters the poll sweep (FRM-001 — this bounds the
    sweep to the top-N and is what keeps the run under the forum's rate limit).
    Emits a ``RuleACandidate`` for each admitted topic whose ``op_interest_kept``
    is still ``NULL`` — Rule A is judged once and needs no JSON fetch (FRM-002).

    Feed-level ``FetchError`` is absorbed into ``error``; surviving feeds are
    still processed to maximise admission (never-lost, FRM-CON-005).

    NOTE on signature: the plan's TASK-016 omitted ``now``, but
    ``admit_topic`` needs a deterministic ``first_seen_at`` that anchors the
    poll schedule, so ``now`` is injected here for testability (FRM-PAT-002).
    This is a faithful refinement of the plan's intent.
    """
    assert site.forum_url is not None, "admit_from_feeds requires a forum site"
    forum_url = site.forum_url

    daily_count = (
        site.daily_watch_count if site.daily_watch_count is not None else DEFAULT_DAILY_WATCH_COUNT
    )
    weekly_count = (
        site.weekly_watch_count
        if site.weekly_watch_count is not None
        else DEFAULT_WEEKLY_WATCH_COUNT
    )

    errors: list[str] = []
    # Count every Discourse HTTP request attempted (incremented before each
    # fetch, so a FetchError still counts the call we made — politeness metric).
    fetch_count = 0

    # --- Fetch all three feeds independently; absorb per-feed FetchErrors ---

    # latest.rss: all entries in source order (sort=False preserves feed order,
    # though for latest the order does not matter — we take all topics, not top-N).
    latest_entries = []
    try:
        fetch_count += 1
        result = fetch(latest_feed_url(forum_url), client=client)
        latest_entries = parse_feed(result.content, result.final_url, sort=False)
    except FetchError as exc:
        errors.append(str(exc))

    # top.rss?period=daily: rank order, truncated to daily_count (FRM-001).
    # parse_feed with sort=False preserves the feed's rank order (FRM-GUD-002).
    daily_entries = []
    try:
        fetch_count += 1
        result = fetch(top_feed_url(forum_url, "daily"), client=client)
        daily_entries = parse_feed(result.content, result.final_url, sort=False)[:daily_count]
    except FetchError as exc:
        errors.append(str(exc))

    # top.rss?period=weekly: rank order, truncated to weekly_count (FRM-001).
    weekly_entries = []
    try:
        fetch_count += 1
        result = fetch(top_feed_url(forum_url, "weekly"), client=client)
        weekly_entries = parse_feed(result.content, result.final_url, sort=False)[:weekly_count]
    except FetchError as exc:
        errors.append(str(exc))

    # --- Poll-eligible set: topics surfaced by the top feeds (FRM-001) ---
    # Only daily/weekly top topics are JSON-polled for Rule B; a latest.rss-only
    # topic is admitted for Rule-A judged-once tracking but not enrolled in the
    # poll schedule, bounding the per-run poll sweep to the top-N (the prior
    # all-latest sweep tripped the forum's anonymous rate limit, 429).
    top_topic_ids: set[int] = set()
    for entry in [*daily_entries, *weekly_entries]:
        tid = topic_id_from_url(entry.canonical_url)
        if tid is not None:
            top_topic_ids.add(tid)

    # --- Build admission union deduped by topic id ---
    # Iteration order: latest first, then daily top, then weekly top.
    # First entry seen for a given topic id supplies the OP text (FRM-002).
    seen_topic_ids: set[int] = set()
    ordered: list[tuple[int, str, str | None, str]] = []  # (topic_id, title, summary, url)

    for entry in [*latest_entries, *daily_entries, *weekly_entries]:
        tid = topic_id_from_url(entry.canonical_url)
        if tid is None or tid in seen_topic_ids:
            continue
        seen_topic_ids.add(tid)
        ordered.append((tid, entry.title or "", entry.summary, str(entry.canonical_url)))

    # --- Admit each topic (idempotent INSERT-OR-IGNORE, FRM-CON-005) ---
    candidates: list[RuleACandidate] = []
    for topic_id, title, summary, topic_url in ordered:
        forum_store.admit_topic(
            conn,
            site.id,
            topic_id,
            first_seen_at=now,
            poll_eligible=topic_id in top_topic_ids,
        )

        # Emit a Rule-A candidate only when op_interest_kept is still NULL
        # (topic not yet judged under Rule A). FRM-002: Rule A is judged once.
        if forum_store.op_interest_kept(conn, site.id, topic_id) is None:
            candidates.append(
                RuleACandidate(
                    topic_id=topic_id,
                    topic_url=topic_url,
                    title=title,
                    op_text=summary,
                )
            )

    return AdmitResult(
        candidates=candidates,
        error="; ".join(errors) if errors else None,
        fetch_count=fetch_count,
    )


# ---------------------------------------------------------------------------
# Rule-B: due-topic candidate assembly (TASK-017/018)
# ---------------------------------------------------------------------------


def gather_forum(
    conn: sqlite3.Connection,
    site: SiteConfig,
    *,
    client: httpx.Client,
    now: int,
) -> GatherForumResult:
    """Assemble Rule-B candidates for due topics. Performs NO writes (FRM-CON-005).

    For each topic returned by ``forum_store.due_topics``:

    1.  **FRM-CON-004 short-circuit**: when ``completed_polls > 0`` (the topic
        has been polled at least once) AND the stored ``last_like_count`` is not
        ``None`` AND the freshly-parsed topic's ``like_count`` equals the stored
        value, skip the per-post deeper scan and emit no candidate.

        IMPORTANT — v1 reconciliation: the plan's FRM-CON-004 described
        "skip the JSON fetch when like_count is unchanged."  In practice the
        current ``like_count`` is only observable by fetching ``/t/<id>.json``
        (the RSS feeds carry no like data — verified against the captured
        fixture).  Therefore this lever saves **judge/scan cost**, not
        bandwidth — the network round-trip cannot be elided in v1.  When
        ``completed_polls == 0`` or ``last_like_count`` is ``None``, we always
        fetch and evaluate; when ``completed_polls > 0`` and the fetched
        ``topic.like_count == row["last_like_count"]``, we emit no candidate
        (short-circuit the deeper scan).  See also RISK-002.

    2.  Compute the **effective like threshold** (TASK-018): use
        ``interest_like_threshold`` (per-site else ``DEFAULT_INTEREST_LIKE_THRESHOLD``)
        when ``row["op_interest_kept"] == 1`` (Rule A kept the topic), else
        ``like_threshold`` (per-site else ``DEFAULT_LIKE_THRESHOLD``).

    3.  Collect **qualifying posts**: ``post.like_count >= effective_threshold``
        AND NOT ``forum_store.is_post_seen(conn, site.id, post.id)`` (FRM-003 /
        FRM-006).  If any qualify, emit one ``RuleBCandidate``.

    After a successful ``parse_topic`` (whether or not the topic is short-
    circuited or has qualifying posts), append a ``PolledTopic`` to the
    ``GatherForumResult.polled_topics`` finalize worklist (FRM-007 / FRM-CON-005
    — Phase 5 review seam: the CLI needs the freshly-parsed ``like_count`` to
    call ``finalize_poll``; topics that raised ``FetchError`` are excluded so
    their poll is not advanced, and they re-poll next run without loss).

    A topic-level ``FetchError`` is absorbed into ``error`` and records nothing
    (FRM-CON-005: the topic will be re-polled next run).  Processing continues
    for remaining due topics.

    Writes nothing to the DB.  The CLI skill calls ``forum-remind`` /
    ``forum-mark-seen`` per post and ``forum-poll-done`` per topic (Phase 5)
    only after all candidates are dispositioned (FRM-CON-005 / FRM-PAT-001).
    """
    assert site.forum_url is not None, "gather_forum requires a forum site"
    forum_url = site.forum_url

    offsets = (
        site.poll_offsets_days if site.poll_offsets_days is not None else DEFAULT_POLL_OFFSETS_DAYS
    )
    like_thr = site.like_threshold if site.like_threshold is not None else DEFAULT_LIKE_THRESHOLD
    interest_thr = (
        site.interest_like_threshold
        if site.interest_like_threshold is not None
        else DEFAULT_INTEREST_LIKE_THRESHOLD
    )

    errors: list[str] = []
    candidates: list[RuleBCandidate] = []
    polled_topics: list[PolledTopic] = []
    # One Discourse topic-JSON request per due topic; counted before the fetch so
    # a FetchError still counts the attempted call (politeness metric).
    fetch_count = 0

    for row in forum_store.due_topics(conn, site.id, offsets, now):
        topic_id = row["topic_id"]

        try:
            fetch_count += 1
            json_bytes = fetch(topic_json_url(forum_url, topic_id), client=client).content
        except FetchError as exc:
            # Absorb per-topic failure; nothing recorded (FRM-CON-005).
            # Do NOT append to polled_topics — the poll must not be advanced for
            # a topic whose JSON fetch failed (it will re-poll next run).
            errors.append(str(exc))
            continue

        topic, posts = parse_topic(json_bytes)

        # Append to the finalize worklist for every topic that was successfully
        # fetched and parsed — including short-circuited topics and topics with
        # no qualifying posts.  The CLI uses this list to call finalize_poll
        # after candidates are dispositioned (FRM-007 / FRM-CON-005).
        polled_topics.append(PolledTopic(topic_id=topic_id, like_count=topic.like_count))

        # FRM-CON-004 short-circuit: when this is not the first poll AND the
        # topic-level like_count is unchanged since the last poll, skip the
        # per-post deeper scan and emit no candidate for this topic.
        # NOTE: the network round-trip is still performed (we need the JSON to
        # read like_count); this lever saves judge cost, not bandwidth (v1
        # limitation — like_count is only observable via /t/<id>.json, not the
        # RSS feeds; see RISK-002).
        if (
            row["completed_polls"] > 0
            and row["last_like_count"] is not None
            and topic.like_count == row["last_like_count"]
        ):
            continue  # short-circuit: no new likes → no new qualifying posts

        # Effective threshold (TASK-018): interest threshold when Rule A kept
        # the OP; default (higher) threshold otherwise (FRM-003).
        effective_threshold = interest_thr if row["op_interest_kept"] == 1 else like_thr

        # Build the topic URL using canonical_url for normalization (FRM-GUD-003).
        # Include the parsed slug so this matches the slugged ``/t/<slug>/<id>``
        # form the Rule-A path carries verbatim from the RSS entry — the same
        # topic must yield the same reminder URL whichever rule surfaces it. Fall
        # back to the slugless ``/t/<id>`` (a valid Discourse redirect) when the
        # payload carried no slug.
        slug = f"{topic.slug}/" if topic.slug else ""
        topic_url = str(canonical_url(f"{forum_url.rstrip('/')}/t/{slug}{topic_id}"))

        # Collect qualifying, unseen posts (FRM-003 / FRM-006).
        trigger_posts: list[TriggerPost] = []
        for post in posts:
            if post.like_count >= effective_threshold and not forum_store.is_post_seen(
                conn, site.id, post.id
            ):
                trigger_posts.append(
                    TriggerPost(
                        post_id=post.id,
                        post_number=post.number,
                        like_count=post.like_count,
                        text=post.text,
                    )
                )

        if trigger_posts:
            candidates.append(
                RuleBCandidate(
                    topic_id=topic_id,
                    topic_url=topic_url,
                    title=topic.title,
                    trigger_posts=trigger_posts,
                    effective_threshold=effective_threshold,
                )
            )

    return GatherForumResult(
        candidates=candidates,
        polled_topics=polled_topics,
        error="; ".join(errors) if errors else None,
        fetch_count=fetch_count,
    )
