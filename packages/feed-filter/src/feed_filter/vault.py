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
held for longer than the wait below, or the `OSError`/`BadInputError` the shared
writer raises itself — so the CLI records nothing seen and the next run retries,
never-lost over never-duplicated. The CLI reports all four the same way;
whichever it is, it is raised before the seen-record.

The write is held under the shared vault lock (`kboat.lock`), so feed-filter and
a K-Boat run cannot interleave over the same vault. A K-Boat CLI refuses a held
vault at once, reporting who has it so its caller can re-run; feed-filter
**waits** a bounded few seconds instead, because it writes one note per process,
so a refusal costs that entry until the next run while a wait costs a moment.
"""

from __future__ import annotations

from pathlib import Path

from feed_filter.canonical import CanonicalUrl
from kboat.lock import vault_lock
from kboat.naming import url_slug
from kboat.schema import FEED
from kboat.write import upsert

# How long one entry's write waits for a K-Boat run to release the vault. A
# bounded courtesy, not a guarantee it will outlast the holder: what it buys is
# that a hold measured in note writes does not cost an entry, while a run of many
# entries cannot stall behind a slow holder for longer than the run is worth. An
# expired wait is not a lost entry — never-lost carries it to the next run.
VAULT_LOCK_WAIT_S = 5.0


class VaultError(Exception):
    """A feed note could not be written durably.

    Raised when `upsert` refuses the write: a different `url` already occupies
    this slug (an astronomically unlikely 48-bit SHA-256 clash between two
    canonical URLs), or the note holds a `url` the reader cannot decode, so the
    note cannot be shown to be this page at all. Both are reported as a
    collision, distinguished by the record's `reason`, and both need a human. An
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
    `read: false` / `dismissed: false` to resurface a re-written topic. It omits
    `shelved`, the reader's "read later" flag, which `upsert` defaults to `false`
    on create and preserves on a re-write. A blank `title` falls back to the URL,
    so the note's required `title` is never empty. Returns `upsert`'s
    `{status, slug, path}`; raises `VaultError` on a slug collision, or
    `VaultLockedError` when `VAULT_LOCK_WAIT_S` passes with another run still holding
    the vault.
    """
    slug = url_slug(str(cu))
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
    with vault_lock(vault, wait_s=VAULT_LOCK_WAIT_S):
        result = upsert(FEED, vault, record, today=today)
    if result.get("status") == "collision":
        if result.get("reason") == "unreadable_identity":
            raise VaultError(
                f"slug {slug} holds a note whose url cannot be read, so it cannot be "
                f"shown to be this page ({result.get('incoming')!r}) — repair the note by hand"
            )
        raise VaultError(
            f"slug {slug} already holds a different url "
            f"({result.get('existing')!r} vs {result.get('incoming')!r})"
        )
    return result
