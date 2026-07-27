"""Forum pipeline — admission union and Rule-B candidate assembly.

This module is the deterministic orchestration layer that turns due forum topics
into judge-ready candidates, mirroring ``pipeline.gather_new``'s read-only,
error-absorbing shape.

Two public entry points:

``admit_from_feeds(conn, site, *, client, now)`` — **Rule-A path**:
    Fetches the three RSS feeds (latest + daily top + weekly top), builds the
    admission union deduped by topic id, calls ``forum_store.admit_topic`` for
    each (idempotent INSERT-OR-IGNORE), and emits a ``RuleACandidate`` for every
    newly-admitted topic whose ``op_interest_kept`` is still ``NULL``.
    Feed-level ``FetchError`` is absorbed into ``error``; surviving feeds are
    still processed.

``gather_forum(conn, site, *, client, now)`` — **Rule-B path**:
    For each due topic (``forum_store.due_topics``), fetches its JSON, evaluates
    qualifying posts against the effective like threshold, and emits a
    ``RuleBCandidate`` carrying the trigger posts and threshold.  Also returns a
    **finalize worklist** (``GatherForumResult.polled_topics``) — one
    ``PolledTopic`` per due topic whose gather *completed* (see that class for
    exactly what completing admits and excludes).  This worklist is the seam the
    CLI needs to call ``finalize_poll`` after candidates are dispositioned, and
    never for a topic that must re-poll — topics whose poll records are not
    advanced would otherwise re-poll every run, growing the watch set
    unboundedly.  Performs **no writes** to the DB; the
    record/finalize path in the CLI writes ``forum_post_seen`` and advances ``completed_polls``
    after all posts are dispositioned.  A topic-level ``FetchError`` is absorbed
    into ``error``; so is an unclassified exception, which additionally sets
    ``unexpected`` and leaves the failed topic out of the worklist.  Remaining
    topics continue either way.

Design constraints honoured here:
- Only forum-adapter tables and code paths are touched.
- No new runtime dependency — httpx + feedparser already present.
- Synchronous throughout; no asyncio.
- Fetch-minimization short-circuit documented inline.
- Never-lost over never-duplicated; error absorbing; no writes in gather.
- ``polled_topics`` worklist enables offset-only retirement without
  advancing polls for topics whose JSON fetch failed transiently; a permanent
  404/410 does advance, so a deleted topic retires instead of re-polling forever.
- Failure isolation is per topic, the grain the never-lost invariant is stated
  at: a topic that did not complete stays out of ``polled_topics`` entirely.
- All HTTP calls through ``fetch.fetch`` / ``FetchError``.
- ``parse_feed(sort=False)`` keeps top-feed rank order.
- ``canonical.canonical_url`` for topic URLs.
- Forum-table SQL lives in ``forum_store``; this module calls its API.
- ``now`` and ``client`` are injected seams for unit testing.
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

# HTTP statuses that mark a topic permanently unfetchable. A deleted Discourse
# topic returns 404 for its ``/t/<id>.json`` indefinitely (observed in the wild:
# the same topic 404'd on every run for weeks); 410 Gone is the HTTP-standard
# "permanently gone" signal. Unlike a throttle / 5xx / transport failure — which
# must re-poll next run (never-lost) — these advance the poll so the
# dead topic retires via the offset schedule instead of being re-fetched
# and re-dismissed every run in perpetuity.
#
# Transient-404 tolerance: a momentary 404 (e.g. a topic briefly unroutable during
# a Discourse deploy) advances only ONE offset and self-corrects on the next
# successful poll; wrongly retiring a still-live topic would take its remaining
# offsets to each 404 in turn. Under the default [0, 1, 7] schedule that tolerance
# is ample; a single-offset schedule has none (one 404 retires). Retirement is
# not reversible (``admit_topic`` never un-retires),
# which is why the multi-offset default matters.
_PERMANENT_FETCH_STATUSES = frozenset({404, 410})

# ---------------------------------------------------------------------------
# Candidate dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleACandidate:
    """A topic surfaced by the RSS discovery feeds for Rule-A OP judgment.

    The OP text comes from the RSS entry's ``summary`` (the feed description).
    No JSON fetch is needed at this stage; the CLI skill decides
    whether to ``WebFetch`` the topic page when the summary is too thin.
    """

    topic_id: int
    topic_url: str
    title: str
    op_text: str | None  # RSS entry summary; None for entries with no description


@dataclass(frozen=True)
class TriggerPost:
    """A single post that crossed the effective like threshold.

    Carried inside ``RuleBCandidate``; the CLI skill judges each post
    independently and records them via ``forum-remind`` / ``forum-mark-seen``.
    """

    post_id: int
    post_number: int
    like_count: int
    text: str


@dataclass(frozen=True)
class RuleBCandidate:
    """A due topic with at least one qualifying, unseen post.

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
    """Outcome of one ``admit_from_feeds`` call.

    ``candidates`` are Rule-A topics whose OP has not yet been judged.
    ``error`` is a combined message from any feed-level ``FetchError``(s),
    or ``None`` if all three feeds fetched successfully.
    ``fetch_count`` is the number of Discourse HTTP requests this call attempted
    (one per RSS feed, so at most three) — counted whether or not each succeeded,
    so the CLI can surface a per-run Discourse-call total for politeness
    observability (see ``cmd_forum_new``).  Default 0 keeps test fakes terse.
    ``all_feeds_failed`` is the typed site-unreachability signal: ``True`` iff
    **every** discovery feed fetch raised ``FetchError``
    (latest ∧ daily-top ∧ weekly-top), so the whole site was unreachable this run.
    It is the trigger the CLI uses to increment the ``site_health`` counter — a
    typed boolean, not a heuristic parse of ``error`` text. A partial failure
    (≥1 feed succeeded) leaves it ``False``, so a reachable-but-degraded site
    resets rather than escalates.  A feed that answered with a body
    ``parse_feed`` could recover nothing from still counts as reachable, since the
    fetch succeeded.  That leaves a known blind spot: a forum whose host answers
    200 with a non-feed page (a moved domain serving a landing page) reports a
    wholly clean status forever, admitting nothing.  The article path catches the
    analogous case with ``zero_links``; the forum path has no zero-admission
    signal yet.  Default ``False`` keeps test fakes terse.
    """

    candidates: list[RuleACandidate]
    error: str | None
    fetch_count: int = 0
    all_feeds_failed: bool = False


@dataclass(frozen=True)
class PolledTopic:
    """A due topic whose gather completed, so its poll may be advanced.

    Carried in ``GatherForumResult.polled_topics``; this is the **finalize
    worklist** the CLI uses to call ``forum_store.finalize_poll`` after
    all of a topic's candidates are dispositioned.  Membership means the topic's
    gather ran to completion — not merely that its JSON parsed, since a topic can
    also fail *after* the parse (the per-post scan reads the DB) and is then
    withheld like any other incomplete one.

    Included: short-circuited topics and topics with no qualifying posts, which
    still need ``finalize_poll`` so ``completed_polls`` advances and they
    eventually retire; and a topic whose fetch raised a *permanent*
    ``FetchError`` (404/410 — a deleted/gone topic), carrying its stored
    ``like_count`` (no fresh parse), so offset-only retirement drains it instead
    of re-polling a dead topic every run forever.

    Excluded: a topic whose fetch raised a *transient* ``FetchError``, and a topic
    whose gather raised at all — their polls must not be advanced, and they
    re-poll next run.
    """

    topic_id: int
    like_count: int  # freshly parsed topic.like_count, stored by finalize_poll


@dataclass(frozen=True)
class GatherForumResult:
    """Outcome of one ``gather_forum`` call.

    ``candidates`` are Rule-B topics with qualifying unseen posts.
    ``polled_topics`` is the finalize worklist — one ``PolledTopic`` per due topic
    whose gather completed (see ``PolledTopic`` for what that admits and excludes).
    ``error`` is a combined message from any topic-level ``FetchError``(s) and any
    unclassified per-topic exception, or ``None`` if every due topic completed.
    ``unexpected`` marks an error the gather could **not** classify — that and no
    more: it says the failure did not arrive as a ``FetchError``, not whose fault
    it is.  It is a typed signal rather than a prefix the reader has to parse back
    out of ``error``, matching ``pipeline.FetchOutcome.unexpected`` on the article
    path.  Default ``False`` keeps test fakes terse.
    ``fetch_count`` is the number of Discourse topic-JSON requests this call
    attempted (one per due topic, counted whether or not each succeeded), so the
    CLI can report a per-run Discourse-call total.  Default 0 keeps fakes terse.
    """

    candidates: list[RuleBCandidate]
    polled_topics: list[PolledTopic]
    error: str | None
    fetch_count: int = 0
    unexpected: bool = False


# ---------------------------------------------------------------------------
# Rule-A: admission union + candidate emission
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
    ``top.rss?period=weekly`` from ``site.forum_url``.  Builds the
    admission union deduped by topic id — a topic surfaced by multiple feeds is
    admitted once; the first entry seen for a given topic id supplies its OP
    text.

    Calls ``forum_store.admit_topic`` for each topic in the union; this is the
    **only** write in the ``forum-new`` path.  A topic is admitted
    ``poll_eligible`` iff it was surfaced by a top feed (daily ∪ weekly): only
    those are JSON-polled for Rule B, so a ``latest.rss``-only topic is judged
    once under Rule A but never enters the poll sweep (this bounds the
    sweep to the top-N and is what keeps the run under the forum's rate limit).
    Emits a ``RuleACandidate`` for each admitted topic whose ``op_interest_kept``
    is still ``NULL`` — Rule A is judged once and needs no JSON fetch.

    Feed-level ``FetchError`` is absorbed into ``error``; surviving feeds are
    still processed to maximise admission (never-lost).

    NOTE on signature: ``now`` is injected here rather than read from the clock
    internally because ``admit_topic`` needs a deterministic ``first_seen_at``
    that anchors the poll schedule; injecting it keeps the schedule testable.
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
    # Site-unreachability signal: set True by any feed that fetches
    # successfully. If all three raise FetchError it stays False, so the site was
    # wholly unreachable this run — the typed trigger for the site_health counter.
    any_feed_succeeded = False
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
        any_feed_succeeded = True
    except FetchError as exc:
        errors.append(str(exc))

    # top.rss?period=daily: rank order, truncated to daily_count.
    # parse_feed with sort=False preserves the feed's rank order.
    daily_entries = []
    try:
        fetch_count += 1
        result = fetch(top_feed_url(forum_url, "daily"), client=client)
        daily_entries = parse_feed(result.content, result.final_url, sort=False)[:daily_count]
        any_feed_succeeded = True
    except FetchError as exc:
        errors.append(str(exc))

    # top.rss?period=weekly: rank order, truncated to weekly_count.
    weekly_entries = []
    try:
        fetch_count += 1
        result = fetch(top_feed_url(forum_url, "weekly"), client=client)
        weekly_entries = parse_feed(result.content, result.final_url, sort=False)[:weekly_count]
        any_feed_succeeded = True
    except FetchError as exc:
        errors.append(str(exc))

    # --- Poll-eligible set: topics surfaced by the top feeds ---
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
    # First entry seen for a given topic id supplies the OP text.
    seen_topic_ids: set[int] = set()
    ordered: list[tuple[int, str, str | None, str]] = []  # (topic_id, title, summary, url)

    for entry in [*latest_entries, *daily_entries, *weekly_entries]:
        tid = topic_id_from_url(entry.canonical_url)
        if tid is None or tid in seen_topic_ids:
            continue
        seen_topic_ids.add(tid)
        ordered.append((tid, entry.title or "", entry.summary, str(entry.canonical_url)))

    # --- Admit each topic (idempotent INSERT-OR-IGNORE) ---
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
        # (topic not yet judged under Rule A). Rule A is judged once.
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
        all_feeds_failed=not any_feed_succeeded,
    )


# ---------------------------------------------------------------------------
# Rule-B: due-topic candidate assembly
# ---------------------------------------------------------------------------


def gather_forum(
    conn: sqlite3.Connection,
    site: SiteConfig,
    *,
    client: httpx.Client,
    now: int,
) -> GatherForumResult:
    """Assemble Rule-B candidates for due topics. Performs NO writes.

    For each topic returned by ``forum_store.due_topics``:

    1.  **Short-circuit**: when ``completed_polls > 0`` (the topic
        has been polled at least once) AND the stored ``last_like_count`` is not
        ``None`` AND the freshly-parsed topic's ``like_count`` equals the stored
        value, skip the per-post deeper scan and emit no candidate.

        IMPORTANT — v1 reconciliation: an earlier design described
        "skip the JSON fetch when like_count is unchanged."  In practice the
        current ``like_count`` is only observable by fetching ``/t/<id>.json``
        (the RSS feeds carry no like data — verified against the captured
        fixture).  Therefore this lever saves **judge/scan cost**, not
        bandwidth — the network round-trip cannot be elided in v1.  When
        ``completed_polls == 0`` or ``last_like_count`` is ``None``, we always
        fetch and evaluate; when ``completed_polls > 0`` and the fetched
        ``topic.like_count == row["last_like_count"]``, we emit no candidate
        (short-circuit the deeper scan).

    2.  Compute the **effective like threshold**: use
        ``interest_like_threshold`` (per-site else ``DEFAULT_INTEREST_LIKE_THRESHOLD``)
        when ``row["op_interest_kept"] == 1`` (Rule A kept the topic), else
        ``like_threshold`` (per-site else ``DEFAULT_LIKE_THRESHOLD``).

    3.  Collect **qualifying posts**: ``post.like_count >= effective_threshold``
        AND NOT ``forum_store.is_post_seen(conn, site.id, post.id)``.
        If any qualify, emit one ``RuleBCandidate``.

    A topic that completes contributes a ``PolledTopic`` to the
    ``GatherForumResult.polled_topics`` finalize worklist and one that does not is
    excluded, so its poll is not advanced and it re-polls next run without loss;
    ``PolledTopic`` documents which failures still count as completing.

    A topic-level ``FetchError`` is absorbed into ``error`` and processing
    continues for the remaining topics.

    An **unclassified** exception is absorbed the same way and at the same grain:
    it costs its own topic, is flagged ``unexpected``, and leaves the topics
    already assembled to reach the caller.  Such a topic contributes only an
    error — ``_TopicOutcome`` is what makes that structural — and re-polls next
    run, having recorded nothing.  Why the grain is the topic, and what
    withholding one costs, are in ARCHITECTURE's forum failure-isolation bullet.

    Writes nothing to the DB.  The CLI skill calls ``forum-remind`` /
    ``forum-mark-seen`` per post and ``forum-poll-done`` per topic
    only after all candidates are dispositioned.
    """
    assert site.forum_url is not None, "gather_forum requires a forum site"
    forum_url = site.forum_url

    offsets = (
        site.poll_offsets_days if site.poll_offsets_days is not None else DEFAULT_POLL_OFFSETS_DAYS
    )

    errors: list[str] = []
    candidates: list[RuleBCandidate] = []
    polled_topics: list[PolledTopic] = []
    unexpected = False
    # One Discourse topic-JSON request per due topic; counted before the call so
    # a failed topic still counts the attempted call (politeness metric).
    fetch_count = 0

    for row in forum_store.due_topics(conn, site.id, offsets, now):
        topic_id = row["topic_id"]
        fetch_count += 1
        try:
            outcome = _gather_topic(conn, site, row, forum_url=forum_url, client=client)
        except sqlite3.Error:
            raise  # the run's failure, not this topic's (same carve-out as the CLI's)
        except Exception as exc:
            # Unclassified failure, contained to this topic. ``Exception`` only; a
            # ``BaseException`` (an interrupt, a ``SystemExit``) is the run's
            # failure, not this topic's, so it propagates.
            #
            # Cost of withholding, and the signal it leaves: ARCHITECTURE's forum
            # failure-isolation bullet.
            errors.append(f"topic {topic_id}: {type(exc).__name__}: {exc}")
            unexpected = True
            continue

        if outcome.polled is not None:
            polled_topics.append(outcome.polled)
        if outcome.candidate is not None:
            candidates.append(outcome.candidate)
        if outcome.error is not None:
            errors.append(outcome.error)

    return GatherForumResult(
        candidates=candidates,
        polled_topics=polled_topics,
        error="; ".join(errors) if errors else None,
        fetch_count=fetch_count,
        unexpected=unexpected,
    )


# ---------------------------------------------------------------------------
# Rule-B: one topic's assembly, the unit the per-topic boundary contains
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _TopicOutcome:
    """One due topic's whole contribution to a ``gather_forum`` result.

    Assembled and returned as a unit so the per-topic boundary is structural: a
    topic that raises returns nothing, rather than leaving a partial contribution
    in the result lists for the handler to unwind.

    On the poll/error axis three shapes are constructed and no other: ``polled``
    alone is the ordinary quiet topic (short-circuited, or no qualifying posts);
    ``error`` alone is the transient fetch failure that must re-poll; ``polled``
    with ``error`` is the dead-topic retirement.  A ``candidate`` rides along with
    its topic's ``polled`` and never without it — on its own it would be
    dispositioned while the topic stayed un-finalized, re-offering the same posts
    next run.
    """

    polled: PolledTopic | None = None
    candidate: RuleBCandidate | None = None
    error: str | None = None


def _gather_topic(
    conn: sqlite3.Connection,
    site: SiteConfig,
    row: forum_store.WatchRow,
    *,
    forum_url: str,
    client: httpx.Client,
) -> _TopicOutcome:
    """Assemble one due topic's poll record and Rule-B candidate.  Writes nothing.

    Fetches ``/t/<id>.json`` once and absorbs a ``FetchError``:

    - **Permanent** (404/410 — a deleted/gone topic) returns a ``PolledTopic``
      carrying the *stored* ``last_like_count`` (no fresh parse) alongside the
      error, so offset-only retirement drains the topic instead of re-fetching a
      dead one every run forever.  The message names the topic, so a bounded,
      self-describing retirement notice is distinguishable from a site outage.
    - **Transient** (throttle / 5xx / transport) returns the error alone.  No
      ``PolledTopic``: the poll must not advance for a topic whose fetch may yet
      succeed (never-lost).

    Anything else propagates to the caller's per-topic boundary.
    """
    topic_id = row["topic_id"]
    try:
        json_bytes = fetch(topic_json_url(forum_url, topic_id), client=client).content
    except FetchError as exc:
        if exc.status in _PERMANENT_FETCH_STATUSES:
            last = row["last_like_count"]
            return _TopicOutcome(
                polled=PolledTopic(topic_id=topic_id, like_count=last if last is not None else 0),
                error=f"retiring dead topic {topic_id}: {exc}",
            )
        return _TopicOutcome(error=str(exc))

    topic, posts = parse_topic(json_bytes)

    # A poll record for every topic successfully fetched and parsed — including
    # short-circuited topics and topics with no qualifying posts.  The CLI uses it
    # to call finalize_poll after candidates are dispositioned.
    polled = PolledTopic(topic_id=topic_id, like_count=topic.like_count)

    # Short-circuit: when this is not the first poll AND the topic-level
    # like_count is unchanged since the last poll, skip the per-post deeper scan
    # and emit no candidate for this topic.
    # NOTE: the network round-trip is still performed (we need the JSON to read
    # like_count); this lever saves judge cost, not bandwidth (v1 limitation —
    # like_count is only observable via /t/<id>.json, not the RSS feeds).
    if (
        row["completed_polls"] > 0
        and row["last_like_count"] is not None
        and topic.like_count == row["last_like_count"]
    ):
        return _TopicOutcome(polled=polled)  # no new likes → no new qualifying posts

    # Effective threshold: interest threshold when Rule A kept the OP; default
    # (higher) threshold otherwise.
    like_thr = site.like_threshold if site.like_threshold is not None else DEFAULT_LIKE_THRESHOLD
    interest_thr = (
        site.interest_like_threshold
        if site.interest_like_threshold is not None
        else DEFAULT_INTEREST_LIKE_THRESHOLD
    )
    effective_threshold = interest_thr if row["op_interest_kept"] == 1 else like_thr

    # Build the topic URL using canonical_url for normalization.
    # Include the parsed slug so this matches the slugged ``/t/<slug>/<id>`` form
    # the Rule-A path carries verbatim from the RSS entry — the same topic must
    # yield the same note URL whichever rule surfaces it. Fall back to the
    # slugless ``/t/<id>`` (a valid Discourse redirect) when the payload carried
    # no slug.
    slug = f"{topic.slug}/" if topic.slug else ""
    topic_url = str(canonical_url(f"{forum_url.rstrip('/')}/t/{slug}{topic_id}"))

    # Collect qualifying, unseen posts.
    trigger_posts = [
        TriggerPost(
            post_id=post.id,
            post_number=post.number,
            like_count=post.like_count,
            text=post.text,
        )
        for post in posts
        if post.like_count >= effective_threshold
        and not forum_store.is_post_seen(conn, site.id, post.id)
    ]

    if not trigger_posts:
        return _TopicOutcome(polled=polled)

    return _TopicOutcome(
        polled=polled,
        candidate=RuleBCandidate(
            topic_id=topic_id,
            topic_url=topic_url,
            title=topic.title,
            trigger_posts=trigger_posts,
            effective_threshold=effective_threshold,
        ),
    )
