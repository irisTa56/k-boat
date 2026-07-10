"""Keep the `kboat-notes` prose schema tables in sync with `kboat.schema`.

`kboat.schema` is the code-authoritative mechanical schema; the `kboat-notes`
skill restates the field list as a `| Property | Meaning |` table per note type
for human semantics. That restatement drifts (a field added to code but not the
table, a reorder, a rename). Both sides are structured — code is data, the table
has a `Property` column — so this asserts the table's fields, in order, equal the
code's `field_names()`, turning a silent doc drift into a failing gate. It does
not check per-field prose (defaults, kinds, enums): those are woven into the
`Meaning` cells and are not machine-comparable here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kboat.schema import KINDLE, REPO, SOURCE, NoteSchema

SKILL = Path(__file__).resolve().parents[1] / ".claude/skills/kboat-notes/SKILL.md"

# `## Source note (…)` etc. — the H2 that introduces each type's Property table.
_SECTION = {"Source note": "source", "Kindle note": "kindle", "Repo note": "repo"}
# Track the section by its H2 heading only; a deeper `###` subheading within a
# section must not reset the current type, so an edit that inserts a subsection
# before the Property table cannot hide it (which would fail the test spuriously).
_HEADING = re.compile(r"^##\s+(.*)")
_TABLE_HEADER = re.compile(r"^\|\s*Property\s*\|\s*Meaning\s*\|")
# The field name is the leading backticked token of the Property cell; a trailing
# annotation (e.g. `` `url` (immutable) ``) must not drop the field, so match the
# first token rather than requiring the whole cell to be one token.
_FIELD_NAME = re.compile(r"`([^`]+)`")


def _parse_doc_tables(text: str) -> dict[str, list[str]]:
    """Return {note_type: [field names]} from each type's first Property table."""
    tables: dict[str, list[str]] = {}
    lines = text.splitlines()
    heading: str | None = None
    i = 0
    while i < len(lines):
        m = _HEADING.match(lines[i])
        if m:
            heading = next((t for k, t in _SECTION.items() if k in m.group(1)), None)
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


DOC_TABLES = _parse_doc_tables(SKILL.read_text())


@pytest.mark.parametrize("schema", [SOURCE, KINDLE, REPO], ids=lambda s: s.type)
def test_doc_table_matches_schema_fields(schema: NoteSchema) -> None:
    doc = DOC_TABLES.get(schema.type)
    assert doc is not None, (
        f"no Property/Meaning table found for {schema.type} in {SKILL.name} — "
        "did a section heading change?"
    )
    assert doc == list(schema.field_names()), (
        f"{schema.type} schema table in {SKILL.name} is out of sync with "
        "kboat.schema (fields differ or are reordered)"
    )
