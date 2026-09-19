"""The vault output sink — write a kept feed entry as a `Feeds/` note.

A kept feed entry becomes a `type: feed` note in the shared Obsidian vault,
written through the `kboat` library's schema-driven writer (`kboat.write.upsert`,
schema `FEED`). The note is hash-named by the entry's canonical URL, so a
re-written topic (a forum topic re-kept when a new qualifying post arrives)
upserts the *same* note idempotently — no duplicate. The re-write **resurfaces**
the topic: it forces the two flags that *hide* a card, `read` and `dismissed`,
back to `false`, so a topic the reader already finished with reappears in the
inbox when it gains new activity. The reader's `shelved` "read later" is instead
preserved — it relocates the card rather than hiding it, so feed-filter omits it
and `upsert` keeps the existing value.

`write_feed_note`'s failure contract is never-lost: a write that cannot complete
raises — `VaultError` for a refused write, `VaultLockedError` for a vault another run
still held when the wait expired, `VaultLockUnavailableError` for a lock that could not
be operated at all, or the `OSError`/`BadInputError` the shared writer raises itself — so the CLI records nothing seen and the next run retries, never-lost
over never-duplicated. The CLI reports all four the same way; whichever it is, it is
raised before the seen-record.

The write is held under the shared vault lock (`kboat.lock`), so feed-filter and a
K-Boat run cannot interleave over the same vault. It takes the lock on the shared
terms — wait a few seconds, then refuse — with no wait of its own to keep in step:
one policy for every writer, and its own never-lost contract carries the entry when
the wait does expire.
"""

from __future__ import annotations

from pathlib import Path

from kboat.canonical import CanonicalUrl
from kboat.frontmatter import FrontmatterError
from kboat.lock import vault_lock
from kboat.naming import note_slug
from kboat.schema import FEED
from kboat.write import WROTE_A_NOTE, WriteStatus, upsert


class VaultError(Exception):
    """A feed note could not be written durably.

    Raised whenever `upsert` refuses the write. The expected refusal is a
    collision: a different `url` already occupies this slug (an astronomically
    unlikely 48-bit SHA-256 clash between two canonical URLs), or the note holds
    a `url` the reader cannot decode, so it cannot be shown to be this page at
    all — distinguished by the record's `reason`, and both needing a human. A note
    iCloud has evicted is refused as the subclass `VaultEvictedError` below. Any
    other refusal is raised too rather than read as a write: what makes never-lost
    hold is that nothing is recorded seen unless a note landed, so a status this
    module does not recognise must not be the one that slips through. Also raised
    when an existing note cannot be read at all, which a human repairs the same way. An
    `OSError` from the writer (disk full, permission, a slug held by something that
    is not a note) is left to propagate; the CLI maps both to a non-zero exit and
    skips the seen-record, so the entry is retried rather than silently lost.
    """


class VaultEvictedError(VaultError):
    """The note at this entry's slug is evicted: iCloud holds it behind a placeholder.

    A refusal like any other `VaultError` — nothing written, nothing recorded seen —
    and the one of them that clears on its own once the note is downloaded, and
    concerns this one note rather than the vault. So the CLI reports it apart,
    the way it reports a held vault, for a run skill to carry on past it rather
    than stop reminding.
    """

    def __init__(self, slug: str, path: str) -> None:
        super().__init__(f"{path} is evicted to an iCloud placeholder — nothing was written")
        self.slug = slug
        self.path = path


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
    `read: false` / `dismissed: false` to resurface a re-written topic. It omits
    `shelved`, the reader's "read later" flag, which `upsert` defaults to `false`
    on create and preserves on a re-write. A blank `title` falls back to the URL,
    so the note's required `title` is never empty. Returns `upsert`'s
    `{status, slug, path}`; raises `VaultError` on a write the writer refused or an
    existing note it could not read, or `VaultLockedError` when the shared wait
    passes with another run still holding the vault.
    """
    slug = note_slug(str(cu))
    record: dict[str, object] = {
        "slug": slug,
        "fields": {
            "type": "feed",
            "title": title.strip() if title and title.strip() else str(cu),
            "url": str(cu),
            "read": False,
            "dismissed": False,
            "wall": wall,
            "feed_kind": feed_kind,
            "site_id": site_id,
            "summary": summary,
        },
    }
    with vault_lock(vault):
        try:
            result = upsert(FEED, vault, record, today=today)
        except (FrontmatterError, UnicodeDecodeError) as exc:
            raise VaultError(
                f"slug {slug}'s existing note cannot be read ({exc}) — repair it by hand"
            ) from exc
    status = result.get("status")
    if status in WROTE_A_NOTE:
        return result
    if status == WriteStatus.EVICTED:
        raise VaultEvictedError(slug, str(result.get("path")))
    if status == WriteStatus.COLLISION:
        if result.get("reason") == "unreadable_identity":
            raise VaultError(
                f"slug {slug} holds a note whose url cannot be read, so it cannot be "
                f"shown to be this page ({result.get('incoming')!r}) — repair the note by hand"
            )
        raise VaultError(
            f"slug {slug} already holds a different url "
            f"({result.get('existing')!r} vs {result.get('incoming')!r})"
        )
    raise VaultError(f"the writer refused the note for {cu}: {result}")
