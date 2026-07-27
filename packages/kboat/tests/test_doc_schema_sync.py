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

# Each note type's Property table lives in its owning skill, under an H2 whose
# text contains the heading below. The K-Boat types are in `kboat-notes`; the
# feed type in `kboat-feed-notes`.
_SPEC: dict[str, tuple[str, str]] = {
    "source": ("kboat-notes", "Source note"),
    "kindle": ("kboat-notes", "Kindle note"),
    "repo": ("kboat-notes", "Repo note"),
    "feed": ("kboat-feed-notes", "Feed note"),
}
# Track the section by its H2 heading only; a deeper `###` subheading within a
# section must not reset the current type, so an edit that inserts a subsection
# before the Property table cannot hide it (which would fail the test spuriously).
_HEADING = re.compile(r"^##\s+(.*)")
_TABLE_HEADER = re.compile(r"^\|\s*Property\s*\|\s*Meaning\s*\|")
# The field name is the leading backticked token of the Property cell; a trailing
# annotation (e.g. `` `url` (immutable) ``) must not drop the field, so match the
# first token rather than requiring the whole cell to be one token.
_FIELD_NAME = re.compile(r"`([^`]+)`")


def _parse_doc_tables(text: str, sections: dict[str, str]) -> dict[str, list[str]]:
    """Return {note_type: [field names]} from each type's first Property table.

    `sections` maps a heading substring to the note type it introduces.
    """
    tables: dict[str, list[str]] = {}
    lines = text.splitlines()
    heading: str | None = None
    i = 0
    while i < len(lines):
        m = _HEADING.match(lines[i])
        if m:
            heading = next((t for k, t in sections.items() if k in m.group(1)), None)
        if heading and heading not in tables and _TABLE_HEADER.match(lines[i]):
            i += 2  # skip the header row and the `| --- | --- |` separator
            fields: list[str] = []
            while i < len(lines) and lines[i].startswith("|"):
                cell = lines[i].split("|")[1].strip()
                cm = _FIELD_NAME.search(cell)
                if cm:
                    fields.append(cm.group(1))
                i += 1
            tables[heading] = fields
            continue
        i += 1
    return tables


def _doc_tables() -> dict[str, list[str]]:
    """All note types' tables, each parsed from its owning skill file."""
    by_file: dict[str, dict[str, str]] = {}
    for note_type, (skill, heading) in _SPEC.items():
        by_file.setdefault(skill, {})[heading] = note_type
    tables: dict[str, list[str]] = {}
    for skill, sections in by_file.items():
        text = (SKILLS / skill / "SKILL.md").read_text()
        tables.update(_parse_doc_tables(text, sections))
    return tables


DOC_TABLES = _doc_tables()


@pytest.mark.parametrize("schema", [SOURCE, KINDLE, REPO, FEED], ids=lambda s: s.type)
def test_doc_table_matches_schema_fields(schema: NoteSchema) -> None:
    skill = _SPEC[schema.type][0]
    doc = DOC_TABLES.get(schema.type)
    assert doc is not None, (
        f"no Property/Meaning table found for {schema.type} in {skill} — "
        "did a section heading change?"
    )
    assert doc == list(schema.field_names()), (
        f"{schema.type} schema table in {skill} is out of sync with "
        "kboat.schema (fields differ or are reordered)"
    )


# The same drift risk, for the two tables `kboat-notes` keeps of things the
# `kboat` package declares in code. Both sit under an H3 and are found by their
# own header row rather than by section, each header being unique in the file.
_STATS_TABLE = re.compile(r"^\|\s*Field\s*\|\s*Meaning\s*\|")
_RULES_TABLE = re.compile(r"^\|\s*Code\s*\|\s*Field\s*\|\s*Rule\s*\|")


def _first_column(text: str, header: re.Pattern[str]) -> list[str]:
    """The leading backticked token of each row's first cell, for one table."""
    lines = text.splitlines()
    matches = [i for i, line in enumerate(lines) if header.match(line)]
    assert len(matches) == 1, (
        f"expected exactly one table matching {header.pattern!r}, found {len(matches)} — "
        "did a header row change, or did a second table adopt the same columns?"
    )
    start = matches[0]
    out: list[str] = []
    for line in lines[start + 2 :]:  # skip the header row and the `| --- |` separator
        if not line.startswith("|"):
            break
        cell = _FIELD_NAME.search(line.split("|")[1].strip())
        if cell:
            out.append(cell.group(1))
    return out


def _kboat_notes() -> str:
    return (SKILLS / "kboat-notes/SKILL.md").read_text()


def test_backlog_stats_table_matches_the_stats_fields() -> None:
    assert _first_column(_kboat_notes(), _STATS_TABLE) == [
        f.name for f in dataclass_fields(Stats)
    ], (
        "the Backlog stats table in kboat-notes is out of sync with kboat.validate.stats.Stats "
        "(fields differ or are reordered) — the JSON keys come from the field order"
    )


def test_cross_field_rules_table_matches_the_emitted_codes() -> None:
    assert _first_column(_kboat_notes(), _RULES_TABLE) == [c.value for c in CrossFieldCode], (
        "the Cross-field rules table in kboat-notes is out of sync with "
        "kboat.validate.core.CrossFieldCode (codes differ or are reordered)"
    )
