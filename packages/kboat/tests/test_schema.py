"""Sanity checks on the declarative schema itself."""

from __future__ import annotations

from kboat.schema import BY_TYPE, DIR_BY_TYPE, FEED, KINDLE, REPO, SOURCE, Field, Kind

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


def test_repo_writer_emits_schema_field_order() -> None:
    # The order now lives only in the schema; the writer reads it.
    from kboat.frontmatter import parse_frontmatter
    from kboat.repos.notes import build_repo_note

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

    note = build_repo_note({f.name: sample(f) for f in REPO.fields})
    assert list(parse_frontmatter(note).keys()) == list(REPO.field_names())
