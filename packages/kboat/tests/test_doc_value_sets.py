"""Keep each skill's enumeration of an emitted value set in sync with the code.

A skill that branches on a value `kboat` emits often lists the whole set where
it branches, and that list is a copy of the code's. The copy drifts silently —
a value added in code inherits whatever the list does with its neighbours, the
failure `.claude/rules/one-owner.md` "The value-set sweep" describes. Where the
code side is a declared set (a `Literal`, an enum) and the doc side lists it
whole, this pins the two against each other, the way `test_doc_schema_sync`
pins the schema tables.

Only a site that enumerates a set whole is pinned: a table, a bullet list, or an
inline list. A site naming one value, or counting the set, is left to the sweep,
and so is what each value obliges its reader to do. Each site is found by a
pattern that must match exactly once in its file, so a site that is reworded out
from under its pattern fails loudly rather than going unchecked. Order is not
compared — prose orders a list for its reader — but a missing, extra, or
repeated value is.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import get_args

import pytest

from kboat.repos.gather import Verdict
from kboat.repos.refresh import CollisionReason, Reason
from kboat.schema import ReadmeMark
from kboat.write import WriteStatus

# tests → kboat → packages → the workspace root, which holds `.claude/skills`.
SKILLS = Path(__file__).resolve().parents[3] / ".claude/skills"

_REPOS_SKILL = "kboat-repos/SKILL.md"
_NOTES_PROCEDURES = "kboat-notes/references/procedures.md"
_REPO_NOTE = "kboat-notes/references/repo-note.md"

_BACKTICKED = re.compile(r"`([^`]+)`")
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


def _inline(path: str, pattern: str, *, split: str | None = None) -> Callable[[], list[str]]:
    """The values an inline list states: `pattern`'s one group, split on `split`,
    or — with no `split` — every backticked token inside it."""

    def extract() -> list[str]:
        _, _, match = _only_match(path, re.compile(pattern))
        span = match.group(1)
        if split is not None:
            return [value.strip() for value in span.split(split)]
        return _BACKTICKED.findall(span)

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


_NOT_OK_VERDICTS = tuple(verdict.value for verdict in Verdict if verdict is not Verdict.OK)

# (the code's set, the site's path, how to read the site's values)
_PINS: dict[str, tuple[tuple[str, ...], str, Callable[[], list[str]]]] = {
    "refresh-reason-bullets": (
        get_args(Reason),
        _REPOS_SKILL,
        _bullets(_REPOS_SKILL, r"^Each `failed` entry carries a `reason`"),
    ),
    "refresh-reason-inline": (
        get_args(Reason),
        _NOTES_PROCEDURES,
        _inline(_NOTES_PROCEDURES, r"each with a `reason`: ([^)]*)\)"),
    ),
    "collision-reason-bullets": (
        get_args(CollisionReason),
        _REPOS_SKILL,
        _bullets(_REPOS_SKILL, r"^Each `rename_collisions` entry carries a `reason`"),
    ),
    "collision-reason-inline": (
        get_args(CollisionReason),
        _NOTES_PROCEDURES,
        _inline(_NOTES_PROCEDURES, r"each entry carrying a `reason` — ([^;]*);"),
    ),
    # Both sites enumerate the verdicts that stop the catalogue path, so `ok` is
    # left out of the set they are held to.
    "gather-verdict-bullets": (
        _NOT_OK_VERDICTS,
        _REPOS_SKILL,
        _bullets(_REPOS_SKILL, r"^- The non-`ok` verdicts:$"),
    ),
    "gather-verdict-inline": (
        _NOT_OK_VERDICTS,
        _REPOS_SKILL,
        _inline(_REPOS_SKILL, r"`gather` returned a non-`ok` verdict — ([^(]*)\("),
    ),
    "repo-readme-inline": (
        tuple(mark.value for mark in ReadmeMark),
        _REPO_NOTE,
        _inline(_REPO_NOTE, r"^\| `readme` \| .*? one of ([^.]*)\."),
    ),
    "write-status-inline": (
        tuple(status.value for status in WriteStatus),
        _REPOS_SKILL,
        _inline(_REPOS_SKILL, r"`\{status: ([^,`]+), \.\.\.\}`", split="|"),
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
