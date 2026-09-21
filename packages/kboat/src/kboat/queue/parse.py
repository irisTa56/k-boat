"""Parse one queue-capture note into its URL and title.

The capture bookmarklet drops one `Queue/*.md` file per page, its body a
`[title](url)` markdown link (see `kboat.bookmarklet`). Draining the queue is
`kboat-ingest`'s job; this module owns only the deterministic, security-relevant
extraction of the URL from that body, so the skill consumes a parsed record rather
than re-deriving the rule each run.

The URL is the text between the link's **last** `](` and its **final** `)`.
Anchoring on the last `](` and the final `)` is deliberate:

- a URL that itself contains parentheses (a Wikipedia `..._(disambiguation)` link)
  stays intact, since its inner `)` come before the closing one; and
- a page title crafted with `](` cannot redirect the extraction, because the real
  link the bookmarklet appends is always the last one.

Both URL and title are page-supplied and so untrusted; ingest validates the URL
downstream. The title is a best-effort fallback only (ingest re-derives a better one
from the page), so its extraction is lenient.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from kboat.frontmatter import strip_frontmatter

_URL_RE = re.compile(r"^https?://\S+$")

# What the capture bookmarklet puts ahead of `Date.now()` in each capture's file
# name (see `kboat.bookmarklet`), which makes the name the capture's timestamp.
CAPTURE_PREFIX = "kboat-queue-"
_CAPTURE_NAME_RE = re.compile(rf"^{re.escape(CAPTURE_PREFIX)}(\d+)\.md$")


def captured_on(name: str) -> date | None:
    """The local calendar day a capture was made, read from its file name.

    None for a name that is not `kboat-queue-<epoch_ms>.md` — a capture made by
    hand, or renamed — and for a timestamp no clock can place, rather than a
    guess: the queue's age is a count of days, and a wrong one is worse than none.
    The local day, because `--today` is the reader's local day too (`kboat.cli`).
    """
    match = _CAPTURE_NAME_RE.match(name)
    if not match:
        return None
    try:
        return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=UTC).astimezone().date()
    except OverflowError, OSError, ValueError:
        return None


@dataclass(frozen=True)
class Capture:
    url: str | None  # the extracted http(s) URL, or None when the body has no link
    title: str  # best-effort link text, "" when none


def parse_capture(text: str) -> Capture:
    """Extract the `(url, title)` of a `[title](url)` capture body.

    Returns `Capture(url=None, title="")` when the body carries no parseable
    `http(s)` link — a malformed capture the caller reports rather than guesses at.
    """
    body = strip_frontmatter(text).strip()
    open_idx = body.rfind("](")
    close_idx = body.rfind(")")
    if open_idx == -1 or close_idx <= open_idx + 1:
        return Capture(url=None, title="")
    url = body[open_idx + 2 : close_idx].strip()
    if not _URL_RE.match(url):
        return Capture(url=None, title="")
    lbrack = body.find("[")
    title = body[lbrack + 1 : open_idx].strip() if 0 <= lbrack < open_idx else ""
    return Capture(url=url, title=title)
