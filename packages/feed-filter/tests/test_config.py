"""Behavior tests for config path resolution and env overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from feed_filter import config


def test_constants() -> None:
    assert config.DEFAULT_PER_SITE_CAP == 20
    assert config.DEFAULT_GLOBAL_CAP == 80


def test_forum_constants() -> None:
    assert config.DEFAULT_LIKE_THRESHOLD == 6
    assert config.DEFAULT_INTEREST_LIKE_THRESHOLD == 3
    assert config.DEFAULT_DAILY_WATCH_COUNT == 3
    assert config.DEFAULT_WEEKLY_WATCH_COUNT == 5
    assert config.DEFAULT_POLL_OFFSETS_DAYS == (0, 1, 7)


def test_package_root_contains_pyproject() -> None:
    # PACKAGE_ROOT must point at the feed-filter package dir (which holds its own
    # pyproject.toml and the gitignored local state), not src/ or the module dir.
    assert (config.PACKAGE_ROOT / "pyproject.toml").is_file()


def test_default_paths_are_package_relative(clean_env: None) -> None:
    assert config.sites_path() == config.PACKAGE_ROOT / "sites.toml"
    assert config.selection_path() == config.PACKAGE_ROOT / "prompts" / "selection.md"
    assert config.db_path() == config.PACKAGE_ROOT / "feed-filter.db"


def test_db_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.db"
    monkeypatch.setenv("FEED_FILTER_DB", str(target))
    assert config.db_path() == target


def test_sites_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.toml"
    monkeypatch.setenv("FEED_FILTER_SITES", str(target))
    assert config.sites_path() == target


def test_selection_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The active selection prompt is gitignored local state, redirectable like the
    # others (FEED_FILTER_SELECTION) — only prompts/selection.example.md is committed.
    target = tmp_path / "elsewhere-selection.md"
    monkeypatch.setenv("FEED_FILTER_SELECTION", str(target))
    assert config.selection_path() == target


def test_vault_path_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The output vault is the shared OBSIDIAN_VAULT_PATH — no repo-relative default.
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
    assert config.vault_path() == tmp_path / "vault"


def test_vault_path_unset_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    with pytest.raises(ValueError, match="OBSIDIAN_VAULT_PATH is not set"):
        config.vault_path()
