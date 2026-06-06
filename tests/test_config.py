"""Behavior tests for config path resolution and env overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from feed_filter import config


def test_constants() -> None:
    assert config.REMINDER_LIST == "Filtered Feeds"
    assert config.DEFAULT_PER_SITE_CAP == 20
    assert config.DEFAULT_GLOBAL_CAP == 80


def test_repo_root_contains_pyproject() -> None:
    # REPO_ROOT must point at the project root, not src/ or the package dir.
    assert (config.REPO_ROOT / "pyproject.toml").is_file()


def test_default_paths_are_repo_relative(clean_env: None) -> None:
    assert config.sites_path() == config.REPO_ROOT / "sites.toml"
    assert config.selection_path() == config.REPO_ROOT / "selection.md"
    assert config.db_path() == config.REPO_ROOT / "feed-filter.db"


def test_db_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.db"
    monkeypatch.setenv("FEED_FILTER_DB", str(target))
    assert config.db_path() == target


def test_sites_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.toml"
    monkeypatch.setenv("FEED_FILTER_SITES", str(target))
    assert config.sites_path() == target


def test_selection_has_no_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # selection.md is intentionally not redirectable; it stays version-controlled.
    monkeypatch.setenv("FEED_FILTER_DB", str(tmp_path / "x.db"))
    monkeypatch.setenv("FEED_FILTER_SITES", str(tmp_path / "x.toml"))
    assert config.selection_path() == config.REPO_ROOT / "selection.md"
