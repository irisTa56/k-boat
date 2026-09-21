"""Keep each skill's enumeration of a value set feed-filter emits in sync with the code.

The same gate as `kboat`'s `test_doc_value_sets`, over feed-filter's sets, and
duplicated rather than shared for the reason `test_entry_points` gives: each
member is tested as a self-contained unit. A skill that branches on a value the
CLI emits often lists the whole set where it branches, and that list drifts
silently from the code's — `.claude/rules/one-owner.md` "The value-set sweep".
Where the code side is declared (a `Literal`, a `TypedDict`) and the doc side
lists it whole, this pins the two against each other.

Only a site that enumerates a set whole is pinned; a site naming one value, or
counting the set, is left to the sweep, and so is what each value obliges its
reader to do. Each site is found by a pattern that must match exactly once in
its file, so a site reworded out from under its pattern fails loudly rather than
going unchecked. Order is not compared, but a missing, extra, or repeated value
is.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import get_args

import pytest

from feed_filter.cli import SiteStatus
from feed_filter.discover import RejectionReason

# tests → feed-filter → packages → the workspace root, which holds `.claude/skills`.
SKILLS = Path(__file__).resolve().parents[3] / ".claude/skills"

_ADD_SITE = "kboat-add-feed-site/SKILL.md"
_FEED_RUN = "kboat-feed-run/SKILL.md"
_FORUM_RUN = "kboat-forum-run/SKILL.md"

_BULLET = re.compile(r"^( *)- (.*)$")


def _only_match(path: str, pattern: re.Pattern[str]) -> tuple[list[str], int, re.Match[str]]:
    lines = (SKILLS / path).read_text(encoding="utf-8").splitlines()
    hits = [(i, m) for i, line in enumerate(lines) if (m := pattern.search(line))]
    assert len(hits) == 1, (
        f"expected exactly one line matching {pattern.pattern!r} in {path}, found "
        f"{len(hits)} — was the site reworded, or did a second one adopt the wording?"
    )
    index, match = hits[0]
    return lines, index, match


def _inline(path: str, pattern: str) -> Callable[[], list[str]]:
    """The values an inline `{a, b, …}` list states: `pattern`'s one group, split on commas."""

    def extract() -> list[str]:
        _, _, match = _only_match(path, re.compile(pattern))
        return [value.strip() for value in match.group(1).split(",")]

    return extract


def _bullets(path: str, lead_in: str) -> Callable[[], list[str]]:
    """The leading backticked value of each item in the first bullet list after
    the one line matching `lead_in`.

    The list's level is its first item's indent; a deeper item is a sub-point of
    the one above it and is skipped. The list ends at a blank line or at a line
    that is not an item of that level. An item at that level with no leading
    backticked value fails, since it is either a value spelled some other way or
    a list this pattern should not have reached.
    """

    def extract() -> list[str]:
        lines, start, _ = _only_match(path, re.compile(lead_in))
        values: list[str] = []
        level: int | None = None
        for line in lines[start + 1 :]:
            bullet = _BULLET.match(line)
            if level is None:
                if bullet is None:
                    continue  # prose between the lead-in and its list
                level = len(bullet.group(1))
            if not line.strip():
                break
            indent = len(line) - len(line.lstrip(" "))
            if indent > level:
                continue
            if indent < level or bullet is None:
                break
            value = re.match(r"`([^`]+)`", bullet.group(2))
            assert value, (
                f"an item of the list after {lead_in!r} in {path} leads with no value: {line!r}"
            )
            values.append(value.group(1))
        return values

    return extract


# Both gathers emit the same `sites[]` keys, so both run skills are pinned against one set.
_SITE_KEYS = tuple(SiteStatus.__required_keys__ | SiteStatus.__optional_keys__)

# (the code's set, the site's path, how to read the site's values)
_PINS: dict[str, tuple[tuple[str, ...], str, Callable[[], list[str]]]] = {
    "rejection-reason-bullets": (
        get_args(RejectionReason),
        _ADD_SITE,
        _bullets(_ADD_SITE, r"^- \*\*`rejection` is set\*\*"),
    ),
    "article-site-keys-output": (
        _SITE_KEYS,
        _FEED_RUN,
        _inline(_FEED_RUN, r"sites: \[\{([^}]*)\}\]"),
    ),
    "article-site-keys-entry": (
        _SITE_KEYS,
        _FEED_RUN,
        _inline(_FEED_RUN, r"Each `sites` entry is `\{([^}]*)\}`"),
    ),
    "forum-site-keys-output": (
        _SITE_KEYS,
        _FORUM_RUN,
        _inline(_FORUM_RUN, r"sites: \[\{([^}]*)\}\]"),
    ),
    "forum-site-keys-entry": (
        _SITE_KEYS,
        _FORUM_RUN,
        _inline(_FORUM_RUN, r"Each `sites` entry is `\{([^}]*)\}`"),
    ),
}


@pytest.mark.parametrize("pin", sorted(_PINS))
def test_doc_enumeration_matches_the_code_set(pin: str) -> None:
    code_set, path, extract = _PINS[pin]
    code = sorted(code_set)
    doc = sorted(extract())
    assert doc == code, (
        f"{pin}: the enumeration in {path} is out of sync with the code — "
        f"missing {sorted(set(code) - set(doc))}, not in the code {sorted(set(doc) - set(code))}, "
        f"doc as read {doc}"
    )
