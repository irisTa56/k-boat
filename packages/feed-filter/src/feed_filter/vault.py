"""The vault output sink — write a kept feed entry as a `Feeds/` note.

feed-filter's survivors used to be pushed into Reminders.app; they now become
`type: feed` notes in the shared Obsidian vault, written through the `kboat`
library's schema-driven writer (`kboat.write.upsert`, schema `FEED`). The note is
hash-named by the entry's canonical URL, so a re-written topic (a re-reminded
forum topic) upserts the *same* note idempotently — no duplicate. The re-write
**resurfaces** the topic: it forces `dismissed` back to `false`, so a topic the
reader dismissed reappears in the inbox when a new qualifying post arrives (the
old Reminders sink re-surfaced the same way). The reader's `shelved` "read later"
is instead preserved — feed-filter omits it, and `upsert` keeps the existing
value.

`write_feed_note` mirrors the old `rem` wrapper's failure contract: a write that
cannot complete raises `VaultError` (or lets the underlying `OSError` from the
atomic write propagate), so the CLI records nothing seen and the next run retries
— never-lost over never-duplicated.
"""

from __future__ import annotations

from pathlib import Path

from feed_filter.canonical import CanonicalUrl
from kboat.naming import url_slug
from kboat.schema import FEED
from kboat.write import upsert


class VaultError(Exception):
    """A feed note could not be written durably.

    Raised on a slug collision — a different `url` already occupies this slug, an
    astronomically unlikely 48-bit SHA-256 clash between two canonical URLs. An
    `OSError` from the atomic write (disk full, permission, an iCloud-evicted
    placeholder) is left to propagate; the CLI maps both to a non-zero exit and
    skips the seen-record, so the entry is retried rather than silently lost.
    """


def write_feed_note(
    vault: Path,
    cu: CanonicalUrl,
    *,
    title: str,
    feed_kind: str,
    site_id: str,
    summary: str,
    wall: bool,
    today: str,
) -> dict[str, object]:
    """Create or update the `Feeds/<slug>.md` note for one kept entry.

    The slug is the canonical URL's hash (the shared `kboat.naming` recipe).
    Writes the fields feed-filter owns — `title`, `wall`, `feed_kind`, `site_id`,
    `summary` (plus `type`/`url`, and `added_date` stamped by `upsert`) — and
    `dismissed: false` to resurface a re-written (re-reminded) topic. It omits
    `shelved`, the reader's "read later" flag, which `upsert` defaults to `false`
    on create and preserves on a re-write. Returns `upsert`'s
    `{status, slug, path}`; raises `VaultError` on a slug collision.
    """
    slug = url_slug(str(cu))
    record: dict[str, object] = {
        "slug": slug,
        "fields": {
            "type": "feed",
            "title": title,
            "url": str(cu),
            "dismissed": False,
            "wall": wall,
            "feed_kind": feed_kind,
            "site_id": site_id,
            "summary": summary,
        },
    }
    result = upsert(FEED, vault, record, today=today)
    if result.get("status") == "collision":
        raise VaultError(
            f"slug {slug} already holds a different url "
            f"({result.get('existing')!r} vs {result.get('incoming')!r})"
        )
    return result
