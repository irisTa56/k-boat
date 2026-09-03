"""Keep each note type's prose schema table in sync with `kboat.schema`.

`kboat.schema` is the code-authoritative mechanical schema; the owning skill
restates the field list as a `| Property | Meaning |` table per note type for
human semantics — `kboat-notes` for the K-Boat types, `kboat-feed-notes` for the
feed type. That restatement drifts (a field added to code but not the table, a
reorder, a rename). Both sides are structured — code is data, the table has a
`Property` column — so this asserts the table's fields, in order, equal the
code's `field_names()`, turning a silent doc drift into a failing gate. It does
not check per-field prose (defaults, kinds, enums): those are woven into the
`Meaning` cells and are not machine-comparable here.
"""

from __future__ import annotations

import re
from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest

from kboat.schema import FEED, KINDLE, REPO, SOURCE, NoteSchema
from kboat.validate.core import CrossFieldCode
from kboat.validate.stats import Stats

# The skills live at the workspace root; this test file sits at
# packages/kboat/tests/, so walk up three levels (tests → kboat → packages → root).
SKILLS = Path(__file__).resolve().parents[3] / ".claude/skills"

# Every table below has a file to itself — `kboat-notes` keeps a reference per
# K-Boat note type plus one for the two code-derived tables, and `kboat-feed-notes`
# keeps the feed type in its SKILL.md — so a table is found by its own header row
# and nothing has to locate the section it sits in. `_first_column` asserts that
# header is unique in the file, which is what makes a second table with the same
# columns a loud failure rather than a silent choice between the two.
_SPEC: dict[str, str] = {
    "source": "kboat-notes/references/source-note.md",
    "kindle": "kboat-notes/references/kindle-note.md",
    "repo": "kboat-notes/references/repo-note.md",
    "feed": "kboat-feed-notes/SKILL.md",
}
_VALIDATION = "kboat-notes/references/validation.md"

_TABLE_HEADER = re.compile(r"^\|\s*Property\s*\|\s*Meaning\s*\|")
_STATS_TABLE = re.compile(r"^\|\s*Field\s*\|\s*Meaning\s*\|")
_RULES_TABLE = re.compile(r"^\|\s*Code\s*\|\s*Field\s*\|\s*Rule\s*\|")
# The name is the leading backticked token of the row's first cell; a trailing
# annotation (e.g. `` `url` (immutable) ``) must not drop the field, so match the
# first token rather than requiring the whole cell to be one token.
_FIELD_NAME = re.compile(r"`([^`]+)`")


def _first_column(path: str, header: re.Pattern[str]) -> list[str]:
    """The leading backticked token of each row's first cell, for one table."""
    lines = (SKILLS / path).read_text().splitlines()
    matches = [i for i, line in enumerate(lines) if header.match(line)]
    assert len(matches) == 1, (
        f"expected exactly one table matching {header.pattern!r} in {path}, found "
        f"{len(matches)} — did a header row change, or did a second table adopt "
        "the same columns?"
    )
    out: list[str] = []
    for line in lines[matches[0] + 2 :]:  # skip the header row and the `| --- |` separator
        if not line.startswith("|"):
            break
        cell = _FIELD_NAME.search(line.split("|")[1].strip())
        if cell:
            out.append(cell.group(1))
    return out


@pytest.mark.parametrize("schema", [SOURCE, KINDLE, REPO, FEED], ids=lambda s: s.type)
def test_doc_table_matches_schema_fields(schema: NoteSchema) -> None:
    path = _SPEC[schema.type]
    assert _first_column(path, _TABLE_HEADER) == list(schema.field_names()), (
        f"{schema.type} schema table in {path} is out of sync with "
        "kboat.schema (fields differ or are reordered)"
    )


# The same drift risk, for the two tables `kboat-notes` keeps of things the
# `kboat` package declares in code. Both sit in its validation reference.
def test_backlog_stats_table_matches_the_stats_fields() -> None:
    assert _first_column(_VALIDATION, _STATS_TABLE) == [f.name for f in dataclass_fields(Stats)], (
        f"the Backlog stats table in {_VALIDATION} is out of sync with kboat.validate.stats.Stats "
        "(fields differ or are reordered) — the JSON keys come from the field order"
    )


def test_cross_field_rules_table_matches_the_emitted_codes() -> None:
    assert _first_column(_VALIDATION, _RULES_TABLE) == [c.value for c in CrossFieldCode], (
        f"the Cross-field rules table in {_VALIDATION} is out of sync with "
        "kboat.validate.core.CrossFieldCode (codes differ or are reordered)"
    )
