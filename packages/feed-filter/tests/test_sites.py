"""Behavior tests for the sites registry: validation, round-trip, atomic writes."""

from __future__ import annotations

from pathlib import Path

import pytest
from tomlkit.items import Table

from feed_filter.sites import (
    SiteConfig,
    _opt_int,
    _opt_int_list,
    add_site,
    load_sites,
    set_enabled,
    update_pattern,
)


def _feed(site_id: str = "f1") -> SiteConfig:
    return SiteConfig(id=site_id, name="Feed Site", feed_url="https://example.com/feed.xml")


def _scrape(site_id: str = "s1", pattern: str = r"^/blog/[^/]+/?$") -> SiteConfig:
    return SiteConfig(
        id=site_id,
        name="Scrape Site",
        index_url="https://example.com/blog",
        article_url_pattern=pattern,
    )


def test_kind_property() -> None:
    assert _feed().kind == "feed"
    assert _scrape().kind == "scrape"


def test_validation_rejects_both_set() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        SiteConfig(
            id="x",
            name="X",
            feed_url="https://e.example.com/feed",
            index_url="https://e.example.com",
            article_url_pattern="^/a/",
        )


def test_validation_rejects_neither_set() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        SiteConfig(id="x", name="X")


def test_validation_rejects_pattern_without_index() -> None:
    with pytest.raises(ValueError, match="index_url is required"):
        SiteConfig(id="x", name="X", article_url_pattern="^/a/")


def test_validation_rejects_empty_id_and_name() -> None:
    with pytest.raises(ValueError, match="id must be non-empty"):
        SiteConfig(id="  ", name="X", feed_url="https://e.example.com/feed")
    with pytest.raises(ValueError, match="name must be non-empty"):
        SiteConfig(id="x", name="", feed_url="https://e.example.com/feed")


def test_blank_optional_is_treated_as_unset() -> None:
    # A half-written row (empty feed_url alongside a real pattern) must read as a
    # valid scrape site, not a confusing "both set" rejection.
    site = SiteConfig(
        id="s",
        name="S",
        feed_url="",
        index_url="https://e.example.com",
        article_url_pattern="^/a/",
    )
    assert site.feed_url is None
    assert site.kind == "scrape"


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_sites(tmp_path / "absent.toml") == []


def test_round_trip_add_then_load(tmp_path: Path) -> None:
    path = tmp_path / "sites.toml"
    add_site(path, _feed())
    add_site(path, _scrape())

    loaded = load_sites(path)
    assert [s.id for s in loaded] == ["f1", "s1"]
    assert loaded[0].feed_url == "https://example.com/feed.xml"
    assert loaded[0].kind == "feed"
    assert loaded[1].index_url == "https://example.com/blog"
    assert loaded[1].article_url_pattern == r"^/blog/[^/]+/?$"
    assert loaded[1].kind == "scrape"


def test_add_site_with_selection_override(tmp_path: Path) -> None:
    path = tmp_path / "sites.toml"
    site = SiteConfig(
        id="f1",
        name="Feed Site",
        feed_url="https://example.com/feed.xml",
        selection="keep only release notes",
    )
    add_site(path, site)
    assert load_sites(path)[0].selection == "keep only release notes"


def test_requires_browser_defaults_false() -> None:
    # Absent flag → httpx path; shape validation is unaffected.
    assert _feed().requires_browser is False
    assert _scrape().requires_browser is False


def test_requires_browser_round_trip(tmp_path: Path) -> None:
    # A flagged feed site survives add→load; the flag does not disturb the
    # exactly-one-of feed/scrape shape.
    path = tmp_path / "sites.toml"
    add_site(
        path,
        SiteConfig(
            id="js",
            name="JS Site",
            feed_url="https://example.com/feed.xml",
            requires_browser=True,
        ),
    )
    loaded = load_sites(path)[0]
    assert loaded.requires_browser is True
    assert loaded.kind == "feed"


def test_requires_browser_round_trip_scrape(tmp_path: Path) -> None:
    # The flag is orthogonal to kind: a flagged scrape site round-trips too.
    path = tmp_path / "sites.toml"
    add_site(
        path,
        SiteConfig(
            id="js-scrape",
            name="JS Scrape",
            index_url="https://example.com/blog",
            article_url_pattern=r"^/blog/[^/]+/?$",
            requires_browser=True,
        ),
    )
    loaded = load_sites(path)[0]
    assert loaded.requires_browser is True
    assert loaded.kind == "scrape"


def test_requires_browser_false_is_not_emitted(tmp_path: Path) -> None:
    # The common (httpx) site keeps a minimal row — no redundant
    # ``requires_browser = false`` line, mirroring the optional-field policy.
    path = tmp_path / "sites.toml"
    add_site(path, _feed())
    assert "requires_browser" not in path.read_text(encoding="utf-8")
    assert load_sites(path)[0].requires_browser is False


def test_add_preserves_existing_requires_browser_row(tmp_path: Path) -> None:
    # Adding a second (plain) site re-parses and re-dumps the whole document; a
    # pre-existing flagged row must survive untouched, and the new row must stay
    # minimal (emit-only-when-true). Guards the serialization policy across the
    # multi-site re-dump the round-trip test only exercises for string fields.
    path = tmp_path / "sites.toml"
    add_site(
        path,
        SiteConfig(
            id="js", name="JS", feed_url="https://e.example.com/a.xml", requires_browser=True
        ),
    )
    add_site(path, _feed(site_id="plain"))

    by_id = {s.id: s for s in load_sites(path)}
    assert by_id["js"].requires_browser is True
    assert by_id["plain"].requires_browser is False
    # The plain row carries no redundant flag line in the persisted file.
    assert path.read_text(encoding="utf-8").count("requires_browser") == 1


def test_enabled_defaults_true_and_is_not_emitted(tmp_path: Path) -> None:
    # A site is enabled by default and its row stays minimal — no redundant
    # ``enabled = true`` line (mirrors the requires_browser policy).
    assert _feed().enabled is True
    path = tmp_path / "sites.toml"
    add_site(path, _feed())
    assert "enabled" not in path.read_text(encoding="utf-8")
    assert load_sites(path)[0].enabled is True


def test_add_site_disabled_round_trip(tmp_path: Path) -> None:
    # add_site must serialize a disabled SiteConfig faithfully (the emit-when-False arm).
    path = tmp_path / "sites.toml"
    add_site(
        path, SiteConfig(id="s", name="S", feed_url="https://e.example.com/f.xml", enabled=False)
    )
    assert "enabled = false" in path.read_text(encoding="utf-8")
    assert load_sites(path)[0].enabled is False


def test_set_enabled_round_trip(tmp_path: Path) -> None:
    # disable writes ``enabled = false``; enable removes the key (back to minimal).
    path = tmp_path / "sites.toml"
    add_site(path, _feed(site_id="s"))

    set_enabled(path, "s", False)
    assert load_sites(path)[0].enabled is False
    assert "enabled = false" in path.read_text(encoding="utf-8")

    set_enabled(path, "s", True)
    assert load_sites(path)[0].enabled is True
    assert "enabled" not in path.read_text(encoding="utf-8")  # key removed on enable


def test_set_enabled_rewrites_only_target(tmp_path: Path) -> None:
    path = tmp_path / "sites.toml"
    add_site(path, _feed(site_id="a"))
    add_site(path, _scrape(site_id="b"))

    set_enabled(path, "a", False)

    by_id = {s.id: s for s in load_sites(path)}
    assert by_id["a"].enabled is False
    assert by_id["b"].enabled is True  # sibling untouched
    assert by_id["b"].article_url_pattern == r"^/blog/[^/]+/?$"


def test_set_enabled_unknown_site_raises(tmp_path: Path) -> None:
    path = tmp_path / "sites.toml"
    add_site(path, _feed(site_id="s"))
    with pytest.raises(KeyError):
        set_enabled(path, "nope", False)


def test_load_rejects_non_bool_enabled(tmp_path: Path) -> None:
    # A hand-edited non-bool ``enabled`` must fail loudly, not coerce.
    path = tmp_path / "sites.toml"
    path.write_text(
        '[[site]]\nid = "s"\nname = "S"\n'
        'feed_url = "https://e.example.com/f.xml"\n'
        'enabled = "no"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="enabled must be a bool"):
        load_sites(path)


def test_load_rejects_non_bool_requires_browser(tmp_path: Path) -> None:
    # A hand-edited string/int value must fail loudly, not coerce to False — the
    # operator would otherwise have no signal that the browser path is off.
    path = tmp_path / "sites.toml"
    path.write_text(
        '[[site]]\nid = "js"\nname = "JS"\n'
        'feed_url = "https://e.example.com/f.xml"\n'
        'requires_browser = "yes"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="requires_browser must be a bool"):
        load_sites(path)


def test_add_site_rejects_duplicate_id(tmp_path: Path) -> None:
    path = tmp_path / "sites.toml"
    add_site(path, _feed(site_id="dup"))
    with pytest.raises(ValueError, match="already exists"):
        add_site(path, _scrape(site_id="dup"))
    # The rejected write must not have appended a second entry.
    assert [s.id for s in load_sites(path)] == ["dup"]


def test_load_rejects_duplicate_ids(tmp_path: Path) -> None:
    # A hand-edited file can contain duplicate ids that add_site never produced;
    # load_sites must surface it loudly rather than routing to the first match.
    path = tmp_path / "sites.toml"
    path.write_text(
        "\n".join(
            (
                "[[site]]",
                'id = "dup"',
                'name = "First"',
                'feed_url = "https://e.example.com/a.xml"',
                "",
                "[[site]]",
                'id = "dup"',
                'name = "Second"',
                'feed_url = "https://e.example.com/b.xml"',
            )
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate site id"):
        load_sites(path)


def test_load_rejects_missing_required_key(tmp_path: Path) -> None:
    # A row lacking id/name is malformed; it must raise a clear ValueError, not
    # leak tomlkit's NonExistentKey (the documented contract).
    path = tmp_path / "sites.toml"
    path.write_text(
        '[[site]]\nname = "No Id"\nfeed_url = "https://e.example.com/a.xml"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing required key 'id'"):
        load_sites(path)


def test_update_pattern_heals_scrape_site_with_blank_feed_url(tmp_path: Path) -> None:
    # A half-written scrape row carrying an empty feed_url still loads as scrape,
    # so self-heal must be able to rewrite its pattern — the guard must
    # agree with load_sites that a blank feed_url is unset.
    path = tmp_path / "sites.toml"
    path.write_text(
        "\n".join(
            (
                "[[site]]",
                'id = "s1"',
                'name = "Scrape"',
                'feed_url = ""',
                'index_url = "https://e.example.com/blog"',
                'article_url_pattern = "^/blog/[^/]+/?$"',
            )
        ),
        encoding="utf-8",
    )
    assert load_sites(path)[0].kind == "scrape"
    update_pattern(path, "s1", r"^/posts/[^/]+/?$")
    assert load_sites(path)[0].article_url_pattern == r"^/posts/[^/]+/?$"


def test_update_pattern_rewrites_only_target(tmp_path: Path) -> None:
    path = tmp_path / "sites.toml"
    add_site(path, _scrape(site_id="s1", pattern=r"^/blog/[^/]+/?$"))
    add_site(path, _scrape(site_id="s2", pattern=r"^/news/[^/]+/?$"))

    update_pattern(path, "s1", r"^/posts/[^/]+/?$")

    by_id = {s.id: s for s in load_sites(path)}
    assert by_id["s1"].article_url_pattern == r"^/posts/[^/]+/?$"
    # The sibling entry is untouched (atomic write preserves other entries).
    assert by_id["s2"].article_url_pattern == r"^/news/[^/]+/?$"
    assert by_id["s2"].name == "Scrape Site"


def test_failed_write_leaves_no_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # If the rename fails mid-write, the temp file must be cleaned up and the
    # error propagated — no half-written .tmp litter, no silent swallow.
    import feed_filter.sites as sites_mod

    def boom(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(sites_mod.os, "replace", boom)
    path = tmp_path / "sites.toml"
    with pytest.raises(OSError, match="disk full"):
        add_site(path, _feed())

    assert not path.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_update_pattern_unknown_site_raises(tmp_path: Path) -> None:
    path = tmp_path / "sites.toml"
    add_site(path, _scrape(site_id="s1"))
    with pytest.raises(KeyError):
        update_pattern(path, "nope", r"^/x/")


def test_update_pattern_rejects_feed_site(tmp_path: Path) -> None:
    # Writing a pattern onto a feed site would corrupt the exactly-one invariant
    # and break load_sites for the whole file — reject it at the writer.
    path = tmp_path / "sites.toml"
    add_site(path, _feed(site_id="f1"))
    with pytest.raises(ValueError, match="scrape sites only"):
        update_pattern(path, "f1", r"^/a/")
    # The feed site is left intact and the file still loads.
    assert load_sites(path)[0].kind == "feed"


# ---------------------------------------------------------------------------
# Forum kind
# ---------------------------------------------------------------------------


def _forum(site_id: str = "fr1") -> SiteConfig:
    return SiteConfig(id=site_id, name="Forum Site", forum_url="https://elixirforum.com")


def _forum_full(site_id: str = "fr2") -> SiteConfig:
    return SiteConfig(
        id=site_id,
        name="Forum Full",
        forum_url="https://erlangforums.com",
        forum_subject="Erlang",
        like_threshold=8,
        interest_like_threshold=4,
        daily_watch_count=5,
        weekly_watch_count=10,
        poll_offsets_days=(0, 2, 14),
    )


def test_forum_kind_property() -> None:
    assert _forum().kind == "forum"


def test_three_way_exactly_one_of_rejects_feed_plus_forum() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        SiteConfig(
            id="x",
            name="X",
            feed_url="https://e.example.com/feed",
            forum_url="https://forum.example.com",
        )


def test_three_way_exactly_one_of_rejects_scrape_plus_forum() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        SiteConfig(
            id="x",
            name="X",
            index_url="https://e.example.com",
            article_url_pattern="^/a/",
            forum_url="https://forum.example.com",
        )


def test_three_way_exactly_one_of_rejects_all_three() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        SiteConfig(
            id="x",
            name="X",
            feed_url="https://e.example.com/feed",
            index_url="https://e.example.com",
            article_url_pattern="^/a/",
            forum_url="https://forum.example.com",
        )


def test_three_way_exactly_one_of_rejects_none_set() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        SiteConfig(id="x", name="X")


def test_forum_tuning_without_forum_url_raises() -> None:
    base_url = "https://e.example.com/feed"
    with pytest.raises(ValueError, match="like_threshold can only be set when forum_url"):
        SiteConfig(id="x", name="X", feed_url=base_url, like_threshold=6)
    with pytest.raises(ValueError, match="interest_like_threshold can only be set when forum_url"):
        SiteConfig(id="x", name="X", feed_url=base_url, interest_like_threshold=3)
    with pytest.raises(ValueError, match="daily_watch_count can only be set when forum_url"):
        SiteConfig(id="x", name="X", feed_url=base_url, daily_watch_count=3)
    with pytest.raises(ValueError, match="weekly_watch_count can only be set when forum_url"):
        SiteConfig(id="x", name="X", feed_url=base_url, weekly_watch_count=5)
    with pytest.raises(ValueError, match="poll_offsets_days can only be set when forum_url"):
        SiteConfig(id="x", name="X", feed_url=base_url, poll_offsets_days=(0, 1, 7))


def test_forum_url_blank_normalization() -> None:
    site = SiteConfig(
        id="x",
        name="X",
        feed_url="https://e.example.com/feed",
        forum_url="   ",
    )
    assert site.forum_url is None
    assert site.kind == "feed"


def test_forum_subject_blank_normalization() -> None:
    site = SiteConfig(
        id="x",
        name="X",
        forum_url="https://forum.example.com",
        forum_subject="",
    )
    assert site.forum_subject is None


def test_forum_round_trip_minimal(tmp_path: Path) -> None:
    path = tmp_path / "sites.toml"
    add_site(path, _forum())
    loaded = load_sites(path)
    assert len(loaded) == 1
    s = loaded[0]
    assert s.id == "fr1"
    assert s.forum_url == "https://elixirforum.com"
    assert s.kind == "forum"
    assert s.forum_subject is None
    assert s.like_threshold is None
    assert s.poll_offsets_days is None


def test_forum_round_trip_with_all_tuning_overrides(tmp_path: Path) -> None:
    path = tmp_path / "sites.toml"
    add_site(path, _forum_full())
    s = load_sites(path)[0]
    assert s.forum_url == "https://erlangforums.com"
    assert s.forum_subject == "Erlang"
    assert s.like_threshold == 8
    assert s.interest_like_threshold == 4
    assert s.daily_watch_count == 5
    assert s.weekly_watch_count == 10
    assert s.poll_offsets_days == (0, 2, 14)
    assert s.kind == "forum"


def test_forum_minimal_row_has_no_tuning_keys(tmp_path: Path) -> None:
    path = tmp_path / "sites.toml"
    add_site(path, _forum())
    text = path.read_text(encoding="utf-8")
    for key in (
        "like_threshold",
        "interest_like_threshold",
        "daily_watch_count",
        "weekly_watch_count",
        "poll_offsets_days",
        "forum_subject",
    ):
        assert key not in text


def test_forum_mixed_registry_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sites.toml"
    add_site(path, _feed())
    add_site(path, _forum())
    add_site(path, _scrape())
    by_id = {s.id: s for s in load_sites(path)}
    assert by_id["f1"].kind == "feed"
    assert by_id["fr1"].kind == "forum"
    assert by_id["s1"].kind == "scrape"


def test_update_pattern_rejects_forum_site(tmp_path: Path) -> None:
    # Self-heal targets scrape sites only; writing a pattern onto a forum site
    # would set both forum_url and article_url_pattern, corrupting the three-way
    # exactly-one-of invariant and breaking load_sites for the whole file.
    path = tmp_path / "sites.toml"
    add_site(path, _forum(site_id="fr1"))
    with pytest.raises(ValueError, match="scrape sites only"):
        update_pattern(path, "fr1", r"^/a/")
    # The forum site is left intact and the file still loads.
    assert load_sites(path)[0].kind == "forum"


# ---------------------------------------------------------------------------
# _opt_int / _opt_int_list helpers
# ---------------------------------------------------------------------------


def _table_from_dict(d: dict) -> Table:
    # The _opt_* helpers only use the mapping ``.get`` interface, which a
    # top-level TOMLDocument satisfies, so we cast one to Table rather than build
    # a nested ``[[site]]`` table just to reach the helpers' element-level branches.
    from typing import cast

    import tomlkit

    doc = tomlkit.parse("")
    for k, v in d.items():
        doc.add(k, v)  # type: ignore[arg-type]
    return cast(Table, doc)


def test_opt_int_absent_returns_none() -> None:
    t = _table_from_dict({})
    assert _opt_int(t, "x") is None  # type: ignore[arg-type]


def test_opt_int_valid_returns_int() -> None:
    t = _table_from_dict({"x": 6})
    assert _opt_int(t, "x") == 6  # type: ignore[arg-type]


def test_opt_int_string_raises() -> None:
    t = _table_from_dict({"x": "6"})
    with pytest.raises(ValueError, match="must be an integer"):
        _opt_int(t, "x")  # type: ignore[arg-type]


def test_opt_int_bool_raises() -> None:
    t = _table_from_dict({"x": True})
    with pytest.raises(ValueError, match="must be an integer"):
        _opt_int(t, "x")  # type: ignore[arg-type]


def test_opt_int_list_absent_returns_none() -> None:
    t = _table_from_dict({})
    assert _opt_int_list(t, "x") is None  # type: ignore[arg-type]


def test_opt_int_list_valid_returns_tuple() -> None:
    t = _table_from_dict({"x": [0, 1, 7]})
    assert _opt_int_list(t, "x") == (0, 1, 7)  # type: ignore[arg-type]


def test_opt_int_list_non_list_raises() -> None:
    t = _table_from_dict({"x": 7})
    with pytest.raises(ValueError, match="must be an array"):
        _opt_int_list(t, "x")  # type: ignore[arg-type]


def test_opt_int_list_non_int_element_raises() -> None:
    t = _table_from_dict({"x": [0, "1", 7]})
    with pytest.raises(ValueError, match="items must be integers"):
        _opt_int_list(t, "x")  # type: ignore[arg-type]


def test_opt_int_list_bool_element_raises() -> None:
    t = _table_from_dict({"x": [0, True, 7]})
    with pytest.raises(ValueError, match="items must be integers"):
        _opt_int_list(t, "x")  # type: ignore[arg-type]


def test_load_rejects_non_int_like_threshold(tmp_path: Path) -> None:
    path = tmp_path / "sites.toml"
    path.write_text(
        '[[site]]\nid = "fr"\nname = "F"\n'
        'forum_url = "https://forum.example.com"\n'
        'like_threshold = "6"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must be an integer"):
        load_sites(path)


def test_load_rejects_non_array_poll_offsets(tmp_path: Path) -> None:
    path = tmp_path / "sites.toml"
    path.write_text(
        '[[site]]\nid = "fr"\nname = "F"\n'
        'forum_url = "https://forum.example.com"\n'
        "poll_offsets_days = 7\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must be an array"):
        load_sites(path)
