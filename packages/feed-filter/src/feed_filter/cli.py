"""The ``feed-filter`` subcommand surface — the sole contract with the skills.

Every subcommand emits one JSON object/array on stdout and returns an exit code;
the skills never reach into the Python internals, they parse this JSON. The
deterministic core (discover, gather, the seen-store, the vault writer) does the
work — the skills supply only the two fuzzy judgments (cluster pick at
registration, keep/drop at run time).

Ordering invariants enforced here, not in the skills:

- **add-site**: snapshot the back-catalog as seen *first* (durable), write
  ``sites.toml`` *last* — a site in config without a snapshot would
  flood the back-catalog on its first run.
- **remind**: write the ``Feeds/`` note *then* ``seen.record`` in one process,
  record only on a successful write — never records an entry whose note could
  not be written. The note is hash-named by the canonical URL, so writing it
  again is idempotent (no duplicate), which is why there is no dedupe window.
- **new-entries**: interleave entries round-robin across sites *before* the global
  cap so a noisy site can't starve the others; truncated entries are
  omitted and left unseen, so they reappear next run.
- **forum-new**: only forum sites (``kind == "forum"``); **new-entries** only
  non-forum sites — neither path ever sees the other's sites.
- **forum-remind**: write the ``Feeds/`` note *then* ``record_post`` (and
  ``set_op_verdict`` for the OP), record only on a successful write. A
  re-reminded topic upserts the *same* hash-named note, so the human's
  ``shelved``/``dismissed`` triage survives and no suppression is needed.
- **forum-poll-done**: must be the **last** call for a topic in a run, after every
  candidate post has been dispositioned.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from itertools import zip_longest
from typing import Any
from urllib.parse import urlsplit

import httpx

from feed_filter import body_cache, site_health
from feed_filter.browser import (
    BrowserFetchError,
    MissingPlaywrightError,
    close_browser,
    require_playwright_for,
    require_playwright_if_needed,
)
from feed_filter.canonical import canonical_url
from feed_filter.config import (
    DEFAULT_GATHER_CONCURRENCY,
    DEFAULT_GLOBAL_CAP,
    DEFAULT_PERSISTENT_FAILURE_RUNS,
    DEFAULT_POLL_OFFSETS_DAYS,
    SUMMARY_PREVIEW_CHARS,
    db_path,
    sites_path,
    vault_path,
)
from feed_filter.discover import DiscoveryCandidate, discover
from feed_filter.fetch import FetchError, build_client
from feed_filter.forum_pipeline import admit_from_feeds, gather_forum
from feed_filter.forum_store import finalize_poll, record_post, set_op_verdict
from feed_filter.pipeline import FetchOutcome, fetch_entries, fetch_site, filter_gathered
from feed_filter.seen import open_db, record, snapshot
from feed_filter.sites import SiteConfig, add_site, load_sites, set_enabled, update_pattern
from feed_filter.vault import VaultError, write_feed_note


def _emit(obj: Any) -> None:
    """Write one JSON document to stdout (unicode preserved for readable titles)."""
    print(json.dumps(obj, ensure_ascii=False))


def _preview(text: str | None, limit: int) -> str | None:
    """First ``limit`` chars of ``text`` with an ellipsis when truncated, else
    ``text`` unchanged (``None`` passes through). The stdout ``summary`` for a feed
    entry whose full body is cached (``body_cache``); the judge pulls the full body
    via ``entry-body``."""
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + "…"


def _candidate_to_dict(c: DiscoveryCandidate) -> dict[str, Any]:
    return {
        "feed_url": c.feed_url,
        "feed_type": c.feed_type,
        "index_url": c.index_url,
        "article_url_pattern": c.article_url_pattern,
        "sample_urls": list(c.sample_urls),
        "entry_count": c.entry_count,
    }


def _site_to_dict(s: SiteConfig) -> dict[str, Any]:
    # list-sites is the JSON projection of the full model the skills read, so a
    # forum site must be as faithfully represented as a feed/scrape one. The
    # forum-run skill needs ``forum_subject`` to exclude the native subject from
    # the Rule-A judgment; it reaches it here, never by reading
    # sites.toml directly (the CLI is the only contract). The tuning fields are
    # included for the same faithful-projection reason (None when unset → the
    # config default applies at use time).
    return {
        "id": s.id,
        "name": s.name,
        "kind": s.kind,
        "feed_url": s.feed_url,
        "index_url": s.index_url,
        "article_url_pattern": s.article_url_pattern,
        "forum_url": s.forum_url,
        "forum_subject": s.forum_subject,
        "like_threshold": s.like_threshold,
        "interest_like_threshold": s.interest_like_threshold,
        "daily_watch_count": s.daily_watch_count,
        "weekly_watch_count": s.weekly_watch_count,
        "poll_offsets_days": (
            list(s.poll_offsets_days) if s.poll_offsets_days is not None else None
        ),
        "selection": s.selection,
        "requires_browser": s.requires_browser,
        "enabled": s.enabled,
    }


def _site_from_args(args: argparse.Namespace) -> SiteConfig:
    """Build (and shape-validate) a SiteConfig from add-site args."""
    return SiteConfig(
        id=args.id,
        name=args.name,
        feed_url=args.feed_url,
        index_url=args.index_url,
        article_url_pattern=args.article_url_pattern,
        selection=args.selection,
        requires_browser=args.requires_browser,
    )


def _select_sites(site_id: str | None) -> list[SiteConfig]:
    """Load the registry, optionally narrowing to one id (KeyError if absent)."""
    sites = load_sites(sites_path())
    if site_id is None:
        return sites
    selected = [s for s in sites if s.id == site_id]
    if not selected:
        raise KeyError(f"no site with id {site_id!r}")
    return selected


def _round_robin(groups: list[list[Any]]) -> list[Any]:
    """Flatten per-site lists by interleaving one item from each in turn.

    With sites A=[a1,a2,a3] and B=[b1], yields [a1,b1,a2,a3]: a later global-cap
    truncation then trims the tail fairly instead of starving whichever site
    happened to sort last.
    """
    out: list[Any] = []
    for tier in zip_longest(*groups):
        out.extend(item for item in tier if item is not None)
    return out


def cmd_discover(args: argparse.Namespace) -> int:
    """Emit ``{candidates, rejection}``; a transport failure exits non-zero."""
    with build_client() as client:
        result = discover(args.url, client=client)  # FetchError propagates → exit 1
    _emit(
        {
            "candidates": [_candidate_to_dict(c) for c in result.candidates],
            "rejection": (
                {"reason": result.rejection.reason, "message": result.rejection.message}
                if result.rejection is not None
                else None
            ),
        }
    )
    return 0


def cmd_add_site(args: argparse.Namespace) -> int:
    """Register a site: snapshot its current entries seen, THEN write config."""
    site = _site_from_args(args)  # shape validation first
    # The on-disk gate can't see this site yet (config is written last), so check
    # the in-memory config before the snapshot — a flagged site on a Playwright-less
    # machine must fail with the friendly message, not a raw ModuleNotFoundError.
    require_playwright_for(site)
    try:
        with build_client() as client:
            entries = fetch_entries(site, client=client)  # full back-catalog (Fetch* → exit 1)
        with contextlib.closing(open_db(db_path())) as conn:
            snapshot(conn, site.id, [e.canonical_url for e in entries])  # durable, before config
        add_site(sites_path(), site)  # config last
    finally:
        close_browser()  # tear down a lazily-launched browser (no-op for httpx sites)
    _emit({"site_id": site.id, "kind": site.kind, "snapshotted": len(entries)})
    return 0


def cmd_list_sites(_args: argparse.Namespace) -> int:
    _emit([_site_to_dict(s) for s in load_sites(sites_path())])
    return 0


def _gather_host_key(site: SiteConfig) -> str:
    """The network host a gather fetch targets (``feed_url`` or ``index_url`` host).

    Sites sharing a host are grouped so they fetch **sequentially within one
    worker**, never concurrently — no host is ever hit by two simultaneous
    requests (crawler politeness); concurrency in ``cmd_new_entries`` is across
    hosts only. Falls back to the site id when the URL carries no netloc, so a
    malformed entry becomes its own group rather than colliding with others under
    an empty key.
    """
    url = site.feed_url or site.index_url or ""
    return urlsplit(url).netloc.lower() or site.id


def _fetch_all(sites: list[SiteConfig], *, client: httpx.Client) -> dict[str, FetchOutcome]:
    """Fetch every site's entries, returning ``{site_id: FetchOutcome}``.

    httpx sites are grouped by host (``_gather_host_key``) and fetched concurrently
    across hosts through a pool bounded at ``DEFAULT_GATHER_CONCURRENCY``; each
    worker fetches its host's sites in turn, so a host is never hit concurrently.
    ``requires_browser`` sites are fetched on the main thread because Playwright's
    sync API is not thread-safe. ``fetch_site`` absorbs a fetch failure into the
    outcome's ``error``; an *unexpected* exception in a worker surfaces
    when its future is drained here, propagating out of the run exactly as the
    former sequential gather would have.
    """
    outcomes: dict[str, FetchOutcome] = {}

    host_groups: dict[str, list[SiteConfig]] = {}
    for site in sites:
        if site.requires_browser:
            continue
        host_groups.setdefault(_gather_host_key(site), []).append(site)

    def fetch_group(group: list[SiteConfig]) -> list[tuple[str, FetchOutcome]]:
        # One host per worker: fetch its sites sequentially (never concurrently).
        return [(s.id, fetch_site(s, client=client)) for s in group]

    if host_groups:
        with ThreadPoolExecutor(
            max_workers=min(DEFAULT_GATHER_CONCURRENCY, len(host_groups))
        ) as pool:
            for group_result in pool.map(fetch_group, host_groups.values()):
                for site_id, outcome in group_result:
                    outcomes[site_id] = outcome

    # Browser sites on the main thread (Playwright is single-threaded).
    for site in sites:
        if site.requires_browser:
            outcomes[site.id] = fetch_site(site, client=client)

    return outcomes


def cmd_new_entries(args: argparse.Namespace) -> int:
    """Gather new entries across non-forum sites, interleave, apply global cap.

    Forum sites (``kind == "forum"``) are excluded so the article-path gather
    never reaches ``fetch_entries``'s ``assert index_url is not None`` guard or
    ``gather_new``'s scrape flag on a forum row.

    **Bounded per-host concurrency.** The gather is two phases. First the
    network-only ``fetch_site`` runs for every httpx site, fetched concurrently
    across hosts via a thread pool bounded at ``DEFAULT_GATHER_CONCURRENCY``; sites
    sharing a host are grouped into one worker and fetched in turn, so no host sees
    two concurrent requests (``_gather_host_key``). ``requires_browser`` sites use
    Playwright, whose sync API is not thread-safe, so they are fetched on the main
    thread. Then the DB-touching ``filter_gathered`` runs **serially on the main
    thread in registry order**, so the seen-store is read single-threaded and the
    round-robin ordering is unchanged — concurrency only collapses the
    network wall-clock, it does not reorder results.

    Each ``sites[]`` entry also carries ``consecutive_failures`` (the durable
    per-site count from ``site_health``) and ``persistent`` (that count has
    reached ``DEFAULT_PERSISTENT_FAILURE_RUNS``), the article-path mirror of
    ``cmd_forum_new``. The count increments only when the site's gather errored
    this run (``gathered.error is not None``) and resets otherwise, so a stateless
    run can escalate a persistent outage at the threshold instead of re-deriving
    "transient" every run. A ``zero_links`` scrape does not increment
    — it is a broken pattern healed by ``heal-site``, not an outage. The CLI never
    auto-disables — escalation is a skill-side notification.
    """
    # Skip disabled sites entirely (no fetch, no error, no push) — they stay in the
    # registry with their seen-store intact for a later enable-site.
    # Exclude forum sites: their gather path is ``cmd_forum_new``.
    sites = [s for s in _select_sites(args.site_id) if s.enabled and s.kind != "forum"]
    # Fail fast before any fetch if a registered site needs the browser but the
    # extra is missing, so a misconfigured run errors cleanly up front.
    require_playwright_if_needed(sites_path())
    groups: list[list[dict[str, Any]]] = []
    site_status: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    try:
        with contextlib.closing(open_db(db_path())) as conn, build_client() as client:
            outcomes = _fetch_all(sites, client=client)
            for site in sites:
                gathered = filter_gathered(conn, site, outcomes[site.id])
                groups.append(
                    [
                        {
                            "site_id": site.id,
                            "url": str(e.canonical_url),
                            "title": e.title,
                            # Full body here; capped to a preview after the global
                            # clamp below, once the emitted set is known.
                            "summary": e.summary,
                            "kind": e.kind,
                        }
                        for e in gathered.entries
                    ]
                )
                # Site-health escalation, the article-path mirror of
                # cmd_forum_new. An article site fetches a single feed/index, so
                # "unreachable this run" is simply a non-None gather error; a
                # ``zero_links`` scrape (a broken pattern healed by heal-site, not an
                # outage) is a distinct condition and does NOT increment. Same store,
                # keyed by site_id.
                if gathered.error is not None:
                    failure_count = site_health.record_failure(conn, site.id)
                else:
                    site_health.record_success(conn, site.id)
                    failure_count = 0
                site_status.append(
                    {
                        "site_id": site.id,
                        "zero_links": gathered.zero_links,
                        "error": gathered.error,
                        "consecutive_failures": failure_count,
                        "persistent": site_health.is_persistent(
                            failure_count, DEFAULT_PERSISTENT_FAILURE_RUNS
                        ),
                    }
                )
            # Interleave + global clamp *before* caching so only the
            # emitted set is cached. Then stash each emitted feed entry's full body
            # in the transient cache and replace the stdout ``summary`` with a short
            # preview, so the full body reaches only the judging haiku (via
            # ``entry-body``), never this run's orchestrator context.
            entries = _round_robin(groups)[: args.global_cap]
            body_cache.replace(conn, [(d["url"], d["summary"]) for d in entries if d["summary"]])
            for d in entries:
                d["summary"] = _preview(d["summary"], SUMMARY_PREVIEW_CHARS)
    finally:
        close_browser()  # tear down a lazily-launched browser (no-op for httpx-only runs)
    _emit({"entries": entries, "sites": site_status})
    return 0


def cmd_entry_body(args: argparse.Namespace) -> int:
    """Emit one gathered entry's full cached body for the judging subagent.

    ``new-entries`` caches each emitted feed entry's full body and puts only a
    preview on stdout; the judge fetches the full body here so it lands
    only in the cheap judge's context. Output is ``{url, body}``; ``body`` is
    ``null`` on a cache miss — a non-feed entry (scrape entries carry no body), an
    interrupted run, or a body evicted by a later ``new-entries`` — and the judge
    falls back to a full-page WebFetch, so never-lost holds.
    """
    with contextlib.closing(open_db(db_path())) as conn:
        body = body_cache.get(conn, args.url)
    _emit({"url": args.url, "body": body})
    return 0


def cmd_remind(args: argparse.Namespace) -> int:
    """Write the kept entry as a ``Feeds/`` note, then record seen (kept=1).

    Vault write **first**, seen-record only on success: a ``VaultError`` (slug
    collision) or ``OSError`` (the atomic write) raises before the record, so an
    entry whose note could not be written stays unseen for the next run to retry
    — the never-lost half. The title falls back to the URL when blank, so the
    note's required ``title`` is never empty.
    """
    cu = canonical_url(args.url)
    title = args.title.strip() if args.title and args.title.strip() else str(cu)
    result = write_feed_note(
        vault_path(),
        cu,
        title=title,
        feed_kind="article",
        site_id=args.site_id,
        summary=args.summary or "",
        wall=args.wall,
        today=args.today,
    )  # VaultError / OSError → exit 1, no record
    with contextlib.closing(open_db(db_path())) as conn:
        record(conn, cu, args.site_id, args.title or None, kept=1)
    _emit({"slug": result["slug"], "url": str(cu), "kept": True, "status": result["status"]})
    return 0


def cmd_mark_seen(args: argparse.Namespace) -> int:
    """Record a dropped entry seen (kept=0); no reminder."""
    cu = canonical_url(args.url)
    with contextlib.closing(open_db(db_path())) as conn:
        record(conn, cu, args.site_id, args.title or None, kept=0)
    _emit({"url": str(cu), "kept": False})
    return 0


def cmd_heal_site(args: argparse.Namespace) -> int:
    """Re-scrape under a new pattern, snapshot the back-catalog, THEN rewrite config.

    Snapshot-first / config-last, mirroring ``cmd_add_site``: the config write
    (``update_pattern``) is the *last* durable side
    effect, so a fetch failure can never leave ``sites.toml`` carrying the new
    pattern with no snapshot under it — that gap would flood the back-catalog on
    the next run. The new pattern is applied via an in-memory ``replace`` so the
    re-scrape uses it without committing it first. The heal files NO list
    reminder — it is an operational notice, not a page; the run routine reports
    the heal in its push summary instead (the list holds only pages).
    """
    site = _select_sites(args.site_id)[0]  # KeyError if id absent — before any side effect
    if not site.enabled:
        # A disabled site is inert (new-entries skips it). Healing would run a gather
        # it should not, and would slip past the on-disk Playwright gate (which now
        # ignores disabled sites) into a raw ModuleNotFoundError. Refuse — enable first.
        raise ValueError(
            f"heal-site targets enabled sites only (site {site.id!r}); enable it first"
        )
    if site.kind != "scrape":
        raise ValueError(f"heal-site targets scrape sites only (site {site.id!r})")
    # The healed site is already on disk, so the on-disk gate sees it: fail fast if
    # it is browser-flagged but the extra is missing before the re-scrape.
    require_playwright_if_needed(sites_path())

    healed_site = replace(site, article_url_pattern=args.pattern)
    try:
        with build_client() as client:
            entries = fetch_entries(healed_site, client=client)  # re-scraped under the NEW pattern
        with contextlib.closing(open_db(db_path())) as conn:
            snapshot(
                conn, site.id, [e.canonical_url for e in entries]
            )  # flood guard, before config
        update_pattern(sites_path(), site.id, args.pattern)  # config last (durable commit)
    finally:
        close_browser()  # tear down a lazily-launched browser (F2: heal-site re-scrapes too)
    _emit({"site_id": site.id, "pattern": args.pattern, "snapshotted": len(entries)})
    return 0


def cmd_disable_site(args: argparse.Namespace) -> int:
    """Stop gathering a site; config and seen-store are preserved (KeyError if absent)."""
    set_enabled(sites_path(), args.site_id, False)
    _emit({"site_id": args.site_id, "enabled": False})
    return 0


def cmd_enable_site(args: argparse.Namespace) -> int:
    """Resume gathering a disabled site (KeyError if absent)."""
    set_enabled(sites_path(), args.site_id, True)
    _emit({"site_id": args.site_id, "enabled": True})
    return 0


def _forum_site_from_args(args: argparse.Namespace) -> SiteConfig:
    """Build (and shape-validate) a SiteConfig for a forum site from add-forum args."""
    return SiteConfig(
        id=args.id,
        name=args.name,
        forum_url=args.forum_url,
        forum_subject=getattr(args, "forum_subject", None),
        like_threshold=getattr(args, "like_threshold", None),
        interest_like_threshold=getattr(args, "interest_like_threshold", None),
        daily_watch_count=getattr(args, "daily_watch_count", None),
        weekly_watch_count=getattr(args, "weekly_watch_count", None),
        poll_offsets_days=(
            tuple(args.poll_offsets_days)
            if getattr(args, "poll_offsets_days", None) is not None
            else None
        ),
    )


def cmd_add_forum(args: argparse.Namespace) -> int:
    """Register a forum site by writing config only.

    Unlike ``cmd_add_site``, there is NO back-catalog snapshot here.  Article
    sites must snapshot first to avoid flooding on the first run.
    Forum sites do not have this risk: admission writes ``forum_watch`` rows at
    run time (``admit_from_feeds``), so registration only writes config and the
    watch set is built incrementally each run.  Snapshotting a forum back-catalog
    eagerly would silently discard every topic that was due for Rule-A judgment on
    the first run — a loss, not a safety measure.
    """
    site = _forum_site_from_args(args)  # shape validation first (ValueError propagates)
    add_site(sites_path(), site)
    _emit({"site_id": site.id, "kind": site.kind, "forum_url": site.forum_url})
    return 0


def cmd_forum_new(args: argparse.Namespace) -> int:
    """Gather Rule-A and Rule-B candidates for forum sites.

    Filters to ``kind == "forum"`` sites so article sites are never passed to
    ``admit_from_feeds`` / ``gather_forum``.

    For each enabled forum site:
    - ``admit_from_feeds``: fetch the three RSS feeds, admit topics, emit Rule-A
      candidates for topics whose OP has not yet been judged (the only write is
      an idempotent INSERT-OR-IGNORE into ``forum_watch``).
    - ``gather_forum``: for each due topic, fetch its JSON, assemble Rule-B
      candidates from qualifying unseen posts.  Performs no writes.

    Candidates from all sites are interleaved round-robin before the global cap
    (reusing ``_round_robin``).  After capping, the ``polls`` worklist
    is emitted for every polled topic that is cap-safe — i.e. every candidate
    for that ``(site_id, topic_id)`` pair survived the cap.  A topic truncated
    by the cap is withheld from ``polls`` so it re-polls next run and no post
    is finalized before it is dispositioned (never-lost).

    The emitted ``discourse_fetches`` is the total Discourse HTTP calls this run
    made (RSS feeds + topic JSON, summed across sites) — a coarse politeness
    metric the skill reports; it excludes the judging subagents' ``WebFetch``.

    Each ``sites[]`` entry also carries ``consecutive_failures`` (the durable
    per-site count from ``site_health``) and ``persistent`` (that count has
    reached ``DEFAULT_PERSISTENT_FAILURE_RUNS``). The count increments only when
    the site was wholly unreachable this run (``admit_result.all_feeds_failed``)
    and resets on any reachable run, so a stateless run can distinguish a
    persistent outage from a one-run blip and escalate at the threshold instead of
    re-deriving "transient" every run. The CLI never
    auto-disables — escalation is a skill-side notification, not an action.
    """
    sites = [s for s in _select_sites(args.site_id) if s.enabled and s.kind == "forum"]
    now = int(time.time())

    groups: list[list[dict[str, Any]]] = []
    # Finalize worklists: list of (site_id, topic_id, like_count) from gather_forum.
    finalize_worklists: list[tuple[str, int, int]] = []
    site_status: list[dict[str, Any]] = []
    # Accumulated across sites; emitted as discourse_fetches (see docstring).
    discourse_fetches = 0

    with contextlib.closing(open_db(db_path())) as conn, build_client() as client:
        for site in sites:
            admit_result = admit_from_feeds(conn, site, client=client, now=now)
            gather_result = gather_forum(conn, site, client=client, now=now)
            discourse_fetches += admit_result.fetch_count + gather_result.fetch_count

            # Serialize Rule-A candidates.
            rule_a = [
                {
                    "site_id": site.id,
                    "topic_id": c.topic_id,
                    "topic_url": c.topic_url,
                    "title": c.title,
                    "rule": "A",
                    "op_text": c.op_text,
                }
                for c in admit_result.candidates
            ]

            # Serialize Rule-B candidates.
            rule_b = [
                {
                    "site_id": site.id,
                    "topic_id": c.topic_id,
                    "topic_url": c.topic_url,
                    "title": c.title,
                    "rule": "B",
                    "effective_threshold": c.effective_threshold,
                    "trigger_posts": [
                        {
                            "post_id": p.post_id,
                            "post_number": p.post_number,
                            "like_count": p.like_count,
                            "text": p.text,
                        }
                        for p in c.trigger_posts
                    ],
                }
                for c in gather_result.candidates
            ]

            # Interleave Rule-A and Rule-B within this site so neither starves
            # the other; cross-site interleaving happens via _round_robin below.
            groups.append(_round_robin([rule_a, rule_b]))

            # Collect the finalize worklist from gather_forum for cap-safety check.
            for pt in gather_result.polled_topics:
                finalize_worklists.append((site.id, pt.topic_id, pt.like_count))

            # Site-health escalation: increment the durable
            # consecutive-failure counter only when the whole site was unreachable
            # (every discovery feed raised FetchError), else reset it. This keys on
            # the typed admit signal, NOT the combined error string — a dead-topic
            # retirement or a partial feed failure leaves ``all_feeds_failed`` False,
            # so a reachable site never false-escalates.
            if admit_result.all_feeds_failed:
                failure_count = site_health.record_failure(conn, site.id)
            else:
                site_health.record_success(conn, site.id)
                failure_count = 0

            # Combine per-path errors into one status entry per site.
            errors = [e for e in [admit_result.error, gather_result.error] if e is not None]
            site_status.append(
                {
                    "site_id": site.id,
                    "error": "; ".join(errors) if errors else None,
                    "consecutive_failures": failure_count,
                    "persistent": site_health.is_persistent(
                        failure_count, DEFAULT_PERSISTENT_FAILURE_RUNS
                    ),
                }
            )

    # Round-robin interleave across sites, then apply the global cap.
    all_candidates = _round_robin(groups)
    topics = all_candidates[: args.global_cap]

    # Cap-safety: emit a poll record for a topic iff every candidate entry bearing
    # its (site_id, topic_id) key survived the cap (never-lost).
    # Count candidates before and after the cap; a key is safe iff the counts match.
    # Topics with zero candidates (short-circuited or no qualifying posts) have
    # count_before == count_after == 0, so they are trivially safe.
    before: Counter[tuple[str, int]] = Counter(
        (c["site_id"], c["topic_id"]) for c in all_candidates
    )
    after: Counter[tuple[str, int]] = Counter((c["site_id"], c["topic_id"]) for c in topics)

    polls: list[dict[str, Any]] = []
    for site_id, topic_id, like_count in finalize_worklists:
        key = (site_id, topic_id)
        if after[key] == before[key]:
            polls.append({"site_id": site_id, "topic_id": topic_id, "like_count": like_count})

    _emit(
        {
            "topics": topics,
            "polls": polls,
            "sites": site_status,
            "discourse_fetches": discourse_fetches,
        }
    )
    return 0


def cmd_forum_remind(args: argparse.Namespace) -> int:
    """Write the kept forum topic as a ``Feeds/`` note, then record the disposition.

    Mirrors the article path's ``cmd_remind`` ordering: the vault write **first**,
    the DB writes only on success. The note carries ``feed_kind: forum``.

    The two dedupe axes are written independently (the two rules
    dedupe per axis; the OP may be taken up under both A and B):

    - ``--post-id`` present (a Rule-B disposition) → ``record_post(kept=1)``
      marks that post seen in the post-grain store (``forum_post_seen``).
    - ``--is-op`` present (the Rule-A OP disposition) → ``set_op_verdict(kept=1)``
      records the topic-grain interest verdict (``forum_watch.op_interest_kept``).

    A Rule-A keep carries ``--is-op`` and **no** ``--post-id``: Rule A reads the
    OP from RSS and never holds its ``post_id``, so it writes only the
    topic-grain verdict and never touches the post-grain seen store. This keeps
    Rule A fetch-free and lets a later Rule-B pass re-judge the OP if it gains
    likes.

    **No open-reminder suppression.** A forum reminder targets the topic top, so
    the same topic taken up twice (the two-axis A/B case, or a cross-run
    re-remind as a later post crosses the like threshold) resolves to the same
    URL, hence the same hash-named note: the second write is an idempotent
    ``upsert`` of that note, not a duplicate. The former ``open_reminder_urls``
    suppression the Reminders sink needed is therefore gone; the disposition is
    recorded on every keep exactly as before.
    """
    cu = canonical_url(args.url)
    title = args.title.strip() if args.title and args.title.strip() else str(cu)
    result = write_feed_note(
        vault_path(),
        cu,
        title=title,
        feed_kind="forum",
        site_id=args.site_id,
        summary=args.summary or "",
        wall=False,
        today=args.today,
    )  # VaultError / OSError → exit 1, no record
    with contextlib.closing(open_db(db_path())) as conn:
        if args.post_id is not None:
            record_post(conn, args.site_id, args.topic_id, args.post_id, kept=1)
        if args.is_op:
            set_op_verdict(conn, args.site_id, args.topic_id, kept=1)
    _emit(
        {
            "slug": result["slug"],
            "url": str(cu),
            "site_id": args.site_id,
            "topic_id": args.topic_id,
            "post_id": args.post_id,
            "kept": True,
            "status": result["status"],
        }
    )
    return 0


def cmd_forum_mark_seen(args: argparse.Namespace) -> int:
    """Record a dropped forum disposition (kept=0); no reminder.

    The two dedupe axes are written independently, exactly as in
    ``cmd_forum_remind`` but with no ``rem add``:

    - ``--post-id`` present (a Rule-B drop) → ``record_post(kept=0)`` marks the
      post seen so it is not re-judged.
    - ``--is-op`` present (a Rule-A OP drop) → ``set_op_verdict(kept=0)`` records
      the topic-grain interest verdict; it writes **no** post-grain seen, so a
      later Rule-B pass can still re-judge the OP if it gains likes.

    A Rule-A drop therefore carries ``--is-op`` and no ``--post-id`` (Rule A holds
    no OP ``post_id``), and never marks the OP seen at post grain.
    """
    with contextlib.closing(open_db(db_path())) as conn:
        if args.post_id is not None:
            record_post(conn, args.site_id, args.topic_id, args.post_id, kept=0)
        if args.is_op:
            set_op_verdict(conn, args.site_id, args.topic_id, kept=0)
    _emit(
        {
            "site_id": args.site_id,
            "topic_id": args.topic_id,
            "post_id": args.post_id,
            "kept": False,
        }
    )
    return 0


def cmd_forum_poll_done(args: argparse.Namespace) -> int:
    """Advance the poll counter for one topic after all its posts are dispositioned.

    This is the **last** call for a topic in a run.
    Resolves ``poll_offsets_days`` from the site config (per-site override else
    the config default loaded by ``SiteConfig`` at construction time).
    """
    site = _select_sites(args.site_id)[0]  # KeyError if absent — before any side effect
    offsets = (
        site.poll_offsets_days if site.poll_offsets_days is not None else DEFAULT_POLL_OFFSETS_DAYS
    )
    with contextlib.closing(open_db(db_path())) as conn:
        finalize_poll(conn, args.site_id, args.topic_id, args.like_count, offsets)
    _emit(
        {
            "site_id": args.site_id,
            "topic_id": args.topic_id,
            "like_count": args.like_count,
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="feed-filter")
    sub = parser.add_subparsers(dest="command", required=True)

    p_discover = sub.add_parser("discover", help="find feed/scrape candidates for a URL")
    p_discover.add_argument("url")
    p_discover.set_defaults(handler=cmd_discover)

    p_add = sub.add_parser("add-site", help="register a site (snapshots seen, then writes config)")
    p_add.add_argument("--id", required=True)
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--feed-url", dest="feed_url")
    p_add.add_argument("--index-url", dest="index_url")
    p_add.add_argument("--article-url-pattern", dest="article_url_pattern")
    p_add.add_argument("--selection")
    p_add.add_argument(
        "--requires-browser",
        dest="requires_browser",
        action="store_true",
        help="fetch this site through the opt-in Playwright path (JS / anti-bot sites)",
    )
    p_add.set_defaults(handler=cmd_add_site)

    p_list = sub.add_parser("list-sites", help="list registered sites")
    p_list.set_defaults(handler=cmd_list_sites)

    p_new = sub.add_parser("new-entries", help="gather new, unseen entries across sites")
    p_new.add_argument("--site-id", dest="site_id")
    p_new.add_argument("--global-cap", dest="global_cap", type=int, default=DEFAULT_GLOBAL_CAP)
    p_new.set_defaults(handler=cmd_new_entries)

    p_body = sub.add_parser(
        "entry-body", help="print one gathered entry's full cached body (for the judge)"
    )
    p_body.add_argument("--url", required=True)
    p_body.set_defaults(handler=cmd_entry_body)

    p_remind = sub.add_parser(
        "remind", help="write a kept entry as a Feeds/ note and record it seen"
    )
    p_remind.add_argument("--site-id", dest="site_id", required=True)
    p_remind.add_argument("--url", required=True)
    p_remind.add_argument("--title", required=True)
    p_remind.add_argument(
        "--summary", default="", help="short summary/snippet for the note (optional)"
    )
    p_remind.add_argument(
        "--wall",
        action="store_true",
        help="the page is behind a login/paywall (judged on its summary alone)",
    )
    p_remind.add_argument("--today", default=date.today().isoformat(), help=argparse.SUPPRESS)
    p_remind.set_defaults(handler=cmd_remind)

    p_mark = sub.add_parser("mark-seen", help="record a dropped entry seen (kept=0)")
    p_mark.add_argument("--site-id", dest="site_id", required=True)
    p_mark.add_argument("--url", required=True)
    p_mark.add_argument("--title", required=True)
    p_mark.set_defaults(handler=cmd_mark_seen)

    p_heal = sub.add_parser("heal-site", help="rewrite a scrape pattern and re-snapshot")
    p_heal.add_argument("--site-id", dest="site_id", required=True)
    p_heal.add_argument("--pattern", required=True)
    p_heal.set_defaults(handler=cmd_heal_site)

    p_disable = sub.add_parser(
        "disable-site", help="stop gathering a site (keeps its config + seen-store)"
    )
    p_disable.add_argument("--site-id", dest="site_id", required=True)
    p_disable.set_defaults(handler=cmd_disable_site)

    p_enable = sub.add_parser("enable-site", help="resume gathering a disabled site")
    p_enable.add_argument("--site-id", dest="site_id", required=True)
    p_enable.set_defaults(handler=cmd_enable_site)

    # --- Forum subcommands ---

    p_add_forum = sub.add_parser(
        "add-forum", help="register a Discourse forum site (writes config only, no snapshot)"
    )
    p_add_forum.add_argument("--id", required=True)
    p_add_forum.add_argument("--name", required=True)
    p_add_forum.add_argument("--forum-url", dest="forum_url", required=True)
    # Optional tuning overrides (all None → config defaults apply at run time).
    p_add_forum.add_argument("--forum-subject", dest="forum_subject")
    p_add_forum.add_argument("--like-threshold", dest="like_threshold", type=int)
    p_add_forum.add_argument("--interest-like-threshold", dest="interest_like_threshold", type=int)
    p_add_forum.add_argument("--daily-watch-count", dest="daily_watch_count", type=int)
    p_add_forum.add_argument("--weekly-watch-count", dest="weekly_watch_count", type=int)
    p_add_forum.add_argument(
        "--poll-offsets-days",
        dest="poll_offsets_days",
        type=int,
        nargs="+",
        help="poll offsets in days (e.g. --poll-offsets-days 0 1 7)",
    )
    p_add_forum.set_defaults(handler=cmd_add_forum)

    p_forum_new = sub.add_parser(
        "forum-new",
        help="gather Rule-A and Rule-B forum candidates across forum sites",
    )
    p_forum_new.add_argument("--site-id", dest="site_id")
    p_forum_new.add_argument(
        "--global-cap", dest="global_cap", type=int, default=DEFAULT_GLOBAL_CAP
    )
    p_forum_new.set_defaults(handler=cmd_forum_new)

    p_forum_remind = sub.add_parser(
        "forum-remind",
        help="create a forum reminder and record the post seen (kept=1)",
    )
    p_forum_remind.add_argument("--site-id", dest="site_id", required=True)
    p_forum_remind.add_argument("--topic-id", dest="topic_id", type=int, required=True)
    # --post-id is optional: a Rule-B disposition passes it (records the post at
    # post grain); a Rule-A OP disposition omits it (records only the topic-grain
    # verdict via --is-op, since Rule A holds no OP post_id).
    p_forum_remind.add_argument("--post-id", dest="post_id", type=int, default=None)
    p_forum_remind.add_argument("--url", required=True)
    p_forum_remind.add_argument("--title", required=True)
    p_forum_remind.add_argument(
        "--summary", default="", help="short summary/snippet for the note (optional)"
    )
    p_forum_remind.add_argument(
        "--is-op",
        dest="is_op",
        action="store_true",
        help="this is the Rule-A OP disposition; records the topic-grain interest verdict",
    )
    p_forum_remind.add_argument("--today", default=date.today().isoformat(), help=argparse.SUPPRESS)
    p_forum_remind.set_defaults(handler=cmd_forum_remind)

    p_forum_mark = sub.add_parser(
        "forum-mark-seen",
        help="record a dropped forum post seen (kept=0); no reminder",
    )
    p_forum_mark.add_argument("--site-id", dest="site_id", required=True)
    p_forum_mark.add_argument("--topic-id", dest="topic_id", type=int, required=True)
    # --post-id optional, mirroring forum-remind: a Rule-B drop passes it; a
    # Rule-A OP drop omits it and records only the topic-grain verdict (--is-op).
    p_forum_mark.add_argument("--post-id", dest="post_id", type=int, default=None)
    p_forum_mark.add_argument("--url", required=True)
    p_forum_mark.add_argument("--title", required=True)
    p_forum_mark.add_argument(
        "--is-op",
        dest="is_op",
        action="store_true",
        help="this is the Rule-A OP disposition; records the topic-grain drop verdict",
    )
    p_forum_mark.set_defaults(handler=cmd_forum_mark_seen)

    p_forum_poll_done = sub.add_parser(
        "forum-poll-done",
        help="advance the poll counter for one topic (call LAST, after all posts are dispositioned)",
    )
    p_forum_poll_done.add_argument("--site-id", dest="site_id", required=True)
    p_forum_poll_done.add_argument("--topic-id", dest="topic_id", type=int, required=True)
    p_forum_poll_done.add_argument("--like-count", dest="like_count", type=int, required=True)
    p_forum_poll_done.set_defaults(handler=cmd_forum_poll_done)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse argv, dispatch, and map operational failures to a non-zero exit.

    Every expected runtime failure becomes a stderr ``error: …`` line and exit 1
    — never a traceback, never a silent success:

    - ``FetchError`` — network / discover transport failure;
    - ``VaultError`` — a feed note could not be written (slug collision);
    - ``ValueError`` — shape/validation (bad site config, non-scrape heal, an
      unset ``OBSIDIAN_VAULT_PATH``);
    - ``KeyError`` — unknown site id;
    - ``OSError`` — filesystem failures from the config writes / db open
      (disk full, permission, atomic-rename failure). Caught for the same reason
      ``rem``'s absence is: a config write that can't complete is an operational
      failure to report, not a stack trace to dump.
    - ``BrowserFetchError`` — a browser-path gather failure that reaches a command
      directly (add-site / heal-site snapshot), the browser analog of ``FetchError``;
    - ``MissingPlaywrightError`` — a ``requires_browser`` site needs the optional
      extra (the message carries the install command).
    """
    args = build_parser().parse_args(argv)
    try:
        exit_code: int = args.handler(args)
    except (
        FetchError,
        BrowserFetchError,
        VaultError,
        ValueError,
        KeyError,
        OSError,
        MissingPlaywrightError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return exit_code
