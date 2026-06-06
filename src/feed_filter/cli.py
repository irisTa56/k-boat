"""The ``feed-filter`` subcommand surface — the sole contract with the skills (PAT-001).

Every subcommand emits one JSON object/array on stdout and returns an exit code;
the skills never reach into the Python internals, they parse this JSON. The
deterministic core (discover, gather, the seen-store, the ``rem`` wrapper) does
the work — the skills supply only the two fuzzy judgments (cluster pick at
registration, keep/drop at run time).

Ordering invariants enforced here, not in the skills:

- **add-site**: snapshot the back-catalog as seen *first* (durable), write
  ``sites.toml`` *last* (REQ-002) — a site in config without a snapshot would
  flood the back-catalog on its first run.
- **remind**: ``rem add`` *then* ``seen.record`` in one process, record only on a
  successful add (REQ-009) — collapses the duplicate window and never records an
  entry that failed to remind.
- **new-entries**: interleave entries round-robin across sites *before* the global
  cap (REQ-010) so a noisy site can't starve the others; truncated entries are
  omitted and left unseen, so they reappear next run.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from itertools import zip_longest
from typing import Any

from feed_filter.canonical import canonical_url
from feed_filter.config import DEFAULT_GLOBAL_CAP, db_path, sites_path
from feed_filter.discover import DiscoveryCandidate, discover
from feed_filter.fetch import FetchError, build_client
from feed_filter.pipeline import fetch_entries, gather_new
from feed_filter.reminders import ReminderError, add_reminder, report
from feed_filter.seen import open_db, record, snapshot
from feed_filter.sites import SiteConfig, add_site, load_sites, update_pattern


def _emit(obj: Any) -> None:
    """Write one JSON document to stdout (unicode preserved for readable titles)."""
    print(json.dumps(obj, ensure_ascii=False))


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
    return {
        "id": s.id,
        "name": s.name,
        "kind": s.kind,
        "feed_url": s.feed_url,
        "index_url": s.index_url,
        "article_url_pattern": s.article_url_pattern,
        "selection": s.selection,
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
    """Flatten per-site lists by interleaving one item from each in turn (REQ-010).

    With sites A=[a1,a2,a3] and B=[b1], yields [a1,b1,a2,a3]: a later global-cap
    truncation then trims the tail fairly instead of starving whichever site
    happened to sort last.
    """
    out: list[Any] = []
    for tier in zip_longest(*groups):
        out.extend(item for item in tier if item is not None)
    return out


def cmd_discover(args: argparse.Namespace) -> int:
    """Emit ``{candidates, rejection}``; a transport failure exits non-zero (CON-006)."""
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
    """Register a site: snapshot its current entries seen, THEN write config (REQ-002)."""
    site = _site_from_args(args)  # shape validation first
    with build_client() as client:
        entries = fetch_entries(site, client=client)  # full back-catalog (FetchError → exit 1)
    with contextlib.closing(open_db(db_path())) as conn:
        snapshot(conn, site.id, [e.canonical_url for e in entries])  # durable, before config
    add_site(sites_path(), site)  # config last
    _emit({"site_id": site.id, "kind": site.kind, "snapshotted": len(entries)})
    return 0


def cmd_list_sites(_args: argparse.Namespace) -> int:
    _emit([_site_to_dict(s) for s in load_sites(sites_path())])
    return 0


def cmd_new_entries(args: argparse.Namespace) -> int:
    """Gather new entries across sites, interleave, then apply the global cap (REQ-010)."""
    sites = _select_sites(args.site_id)
    groups: list[list[dict[str, Any]]] = []
    site_status: list[dict[str, Any]] = []
    with contextlib.closing(open_db(db_path())) as conn, build_client() as client:
        for site in sites:
            gathered = gather_new(conn, site, client=client)
            groups.append(
                [
                    {
                        "site_id": site.id,
                        "url": str(e.canonical_url),
                        "title": e.title,
                        "summary": e.summary,
                        "kind": e.kind,
                    }
                    for e in gathered.entries
                ]
            )
            site_status.append(
                {"site_id": site.id, "zero_links": gathered.zero_links, "error": gathered.error}
            )
    entries = _round_robin(groups)[: args.global_cap]
    _emit({"entries": entries, "sites": site_status})
    return 0


def cmd_remind(args: argparse.Namespace) -> int:
    """Create a reminder, then record seen (kept=1) only on success (REQ-009)."""
    reminder_id = add_reminder(
        args.title, args.url, args.notes
    )  # ReminderError → exit 1, no record
    cu = canonical_url(args.url)
    with contextlib.closing(open_db(db_path())) as conn:
        record(conn, cu, args.site_id, args.title or None, kept=1)
    _emit({"id": reminder_id, "url": str(cu), "kept": True})
    return 0


def cmd_mark_seen(args: argparse.Namespace) -> int:
    """Record a dropped entry seen (kept=0); no reminder."""
    cu = canonical_url(args.url)
    with contextlib.closing(open_db(db_path())) as conn:
        record(conn, cu, args.site_id, args.title or None, kept=0)
    _emit({"url": str(cu), "kept": False})
    return 0


def cmd_heal_site(args: argparse.Namespace) -> int:
    """Re-scrape under a new pattern, snapshot the back-catalog, THEN rewrite config (REQ-006).

    Snapshot-first / config-last, mirroring ``cmd_add_site`` and the REQ-002
    principle: the config write (``update_pattern``) is the *last* durable side
    effect, so a fetch failure can never leave ``sites.toml`` carrying the new
    pattern with no snapshot under it — that gap would flood the back-catalog on
    the next run. The new pattern is applied via an in-memory ``replace`` so the
    re-scrape uses it without committing it first. ``report`` runs after both, so
    a failed alert (the safe failure direction) cannot cost the flood guard.
    """
    site = _select_sites(args.site_id)[0]  # KeyError if id absent — before any side effect
    if site.kind != "scrape":
        raise ValueError(f"heal-site targets scrape sites only (site {site.id!r})")

    healed_site = replace(site, article_url_pattern=args.pattern)
    with build_client() as client:
        entries = fetch_entries(healed_site, client=client)  # re-scraped under the NEW pattern
    with contextlib.closing(open_db(db_path())) as conn:
        snapshot(conn, site.id, [e.canonical_url for e in entries])  # flood guard, before config
    update_pattern(sites_path(), site.id, args.pattern)  # config last (durable commit)
    reminder_id = report(
        f"healed {site.id}: pattern -> {args.pattern} ({len(entries)} urls snapshotted)"
    )
    _emit(
        {
            "site_id": site.id,
            "pattern": args.pattern,
            "snapshotted": len(entries),
            "reminder_id": reminder_id,
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
    p_add.set_defaults(handler=cmd_add_site)

    p_list = sub.add_parser("list-sites", help="list registered sites")
    p_list.set_defaults(handler=cmd_list_sites)

    p_new = sub.add_parser("new-entries", help="gather new, unseen entries across sites")
    p_new.add_argument("--site-id", dest="site_id")
    p_new.add_argument("--global-cap", dest="global_cap", type=int, default=DEFAULT_GLOBAL_CAP)
    p_new.set_defaults(handler=cmd_new_entries)

    p_remind = sub.add_parser("remind", help="create a reminder and record it seen (kept)")
    p_remind.add_argument("--site-id", dest="site_id", required=True)
    p_remind.add_argument("--url", required=True)
    p_remind.add_argument("--title", required=True)
    p_remind.add_argument("--notes", required=True)
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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse argv, dispatch, and map operational failures to a non-zero exit.

    Every expected runtime failure becomes a stderr ``error: …`` line and exit 1
    — never a traceback, never a silent success:

    - ``FetchError`` — network / discover transport failure (CON-006);
    - ``ReminderError`` — ``rem`` non-zero exit or absent binary (CON-004);
    - ``ValueError`` — shape/validation (bad site config, non-scrape heal);
    - ``KeyError`` — unknown site id;
    - ``OSError`` — filesystem failures from the config writes / db open
      (disk full, permission, atomic-rename failure). Caught for the same reason
      ``rem``'s absence is: a config write that can't complete is an operational
      failure to report, not a stack trace to dump.
    """
    args = build_parser().parse_args(argv)
    try:
        exit_code: int = args.handler(args)
    except (FetchError, ReminderError, ValueError, KeyError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return exit_code
