"""Sanity checks on the declarative schema itself."""

from __future__ import annotations

import ast
from pathlib import Path

import kboat
import kboat.schema
from kboat.schema import (
    BY_TYPE,
    DAILY_DIR,
    DIR_BY_TYPE,
    FEED,
    KINDLE,
    PDFS_DIR,
    QUESTIONS_FILE,
    QUEUE_DIR,
    REPO,
    REVIEWS_DIR,
    SOURCE,
    Field,
    Kind,
)

_ALL = (SOURCE, KINDLE, REPO, FEED)


def test_by_type_covers_all_types() -> None:
    assert set(BY_TYPE) == {"source", "kindle", "repo", "feed"}
    assert set(DIR_BY_TYPE) == set(BY_TYPE)


def test_each_schema_has_a_matching_type_enum() -> None:
    for schema in _ALL:
        type_field = schema.get("type")
        assert type_field is not None
        assert type_field.kind is Kind.ENUM
        assert type_field.enum == (schema.type,)


def test_no_duplicate_field_names() -> None:
    for schema in _ALL:
        names = schema.field_names()
        assert len(names) == len(set(names)), f"duplicate field in {schema.type}"


def test_booleans_are_never_empty_ok() -> None:
    # The always-present-boolean invariant: a BOOL must carry a real value.
    for schema in _ALL:
        for f in schema.fields:
            if f.kind is Kind.BOOL:
                assert not f.empty_ok and f.present, f"{schema.type}.{f.name}"


def test_build_note_emits_schema_field_order() -> None:
    # The order lives only in the schema; the writer reads it.
    from kboat.frontmatter import parse_frontmatter
    from kboat.write import build_note

    def sample(f: Field) -> object:
        if f.kind is Kind.BOOL:
            return False
        if f.kind is Kind.INT:
            return 0
        if f.kind is Kind.STR_LIST:
            return []
        if f.kind is Kind.ENUM:
            return f.enum[0]
        return "x"

    note = build_note(REPO, {f.name: sample(f) for f in REPO.fields})
    assert list(parse_frontmatter(note).keys()) == list(REPO.field_names())


def test_no_module_spells_a_vault_path_of_its_own() -> None:
    """The layout is declared here once, so no module may re-spell one of its values.

    `DIR_BY_TYPE` and the paths beside it exist because two tools that each write
    `"Sources"` cannot be moved together. Nothing catches a regression to a literal on
    its own — the string is identical, so every test still passes — and a merge
    resolution that reverts one is exactly how it would happen. This is that gate.

    **What it matches**: every string constant in the parsed AST equal to a declared
    value, or to one with a trailing slash — the second form is what an f-string leaves
    behind, so `f"Sources/{slug}.md"` is caught where `f"{DIR_BY_TYPE[t]}/{slug}.md"` is
    the intended spelling. Comments are invisible (they are not in the AST), and prose
    survives on whole-string equality rather than on being prose: a docstring or a
    report key *is* a constant and *is* compared, it simply never equals `"Sources"`.

    **What it misses**, so nobody reads it as more than it is: a literal that is only
    part of the spelling. A mid-path f-string run leaves `"/Sources/"`, a glob pattern
    leaves `"Sources/*.md"`, and a split literal leaves neither half — none of those
    equal a declared value. So does a path taken from a variable. Plain concatenation
    *is* caught, since `"Sources" + "/" + slug` still contains the constant. The
    `feed_filter` member is walked by nothing; it spells no vault path today because it
    writes through `upsert`.

    If this ever fires on a string that is genuinely not a path — a report key, an enum
    value that happens to collide — the value is the thing to rename, or this test is
    the place to record why it is exempt. Do not spell a layout value here to silence it.
    """
    declared = {*DIR_BY_TYPE.values(), QUEUE_DIR, REVIEWS_DIR, PDFS_DIR, QUESTIONS_FILE, DAILY_DIR}
    # An f-string's literal run keeps the separator, so both forms are the same mistake.
    spellings = declared | {f"{value}/" for value in declared}
    src = Path(kboat.__file__).parent
    schema_module = Path(kboat.schema.__file__)

    offenders: list[str] = []
    for path in sorted(src.rglob("*.py")):
        if path == schema_module:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in spellings:
                offenders.append(f"{path.relative_to(src)}:{node.lineno}: {node.value!r}")

    assert not offenders, "spell these through kboat.schema instead:\n" + "\n".join(offenders)
