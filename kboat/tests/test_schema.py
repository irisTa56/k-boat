"""Sanity checks on the declarative schema itself."""

from __future__ import annotations

from kboat.schema import BY_TYPE, DIR_BY_TYPE, KINDLE, REPO, SOURCE, Kind


def test_by_type_covers_all_three() -> None:
    assert set(BY_TYPE) == {"source", "kindle", "repo"}
    assert set(DIR_BY_TYPE) == set(BY_TYPE)


def test_each_schema_has_a_matching_type_enum() -> None:
    for schema in (SOURCE, KINDLE, REPO):
        type_field = schema.get("type")
        assert type_field is not None
        assert type_field.kind is Kind.ENUM
        assert type_field.enum == (schema.type,)


def test_no_duplicate_field_names() -> None:
    for schema in (SOURCE, KINDLE, REPO):
        names = schema.field_names()
        assert len(names) == len(set(names)), f"duplicate field in {schema.type}"


def test_booleans_are_never_empty_ok() -> None:
    # The always-present-boolean invariant: a BOOL must carry a real value.
    for schema in (SOURCE, KINDLE, REPO):
        for f in schema.fields:
            if f.kind is Kind.BOOL:
                assert not f.empty_ok and f.present, f"{schema.type}.{f.name}"


def test_repo_field_order_matches_the_writer() -> None:
    from kboat.repos.notes import FIELD_ORDER

    assert REPO.field_names() == FIELD_ORDER
