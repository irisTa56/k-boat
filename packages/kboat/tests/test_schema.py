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
    its own — the string is identical, so every test still passes — and a conflict
    resolution that reverts one is exactly how it would happen. This is that gate.

    Asserted over parsed constants rather than raw text, so prose that names a folder
    (a docstring, a comment, a report key) is untouched: only a string a module would
    actually resolve a path from counts.
    """
    declared = {*DIR_BY_TYPE.values(), QUEUE_DIR, REVIEWS_DIR, PDFS_DIR, QUESTIONS_FILE, DAILY_DIR}
    src = Path(kboat.__file__).parent
    schema_module = Path(kboat.schema.__file__)

    offenders: list[str] = []
    for path in sorted(src.rglob("*.py")):
        if path == schema_module:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in declared:
                offenders.append(f"{path.relative_to(src)}:{node.lineno}: {node.value!r}")

    assert not offenders, "spell these through kboat.schema instead:\n" + "\n".join(offenders)
