"""Tests for the `kboat-bookmarklet` generator."""

from __future__ import annotations

import pytest

from kboat.bookmarklet.__main__ import build_bookmarklet, main


def test_bookmarklet_shape_and_baked_in_params() -> None:
    out = build_bookmarklet("K-Boat", "Queue")
    assert out.startswith("javascript:")
    # The vault name is embedded as a JSON string literal inside encodeURIComponent.
    assert 'encodeURIComponent("K-Boat")' in out
    # The queue folder becomes a "<folder>/" prefix on the timestamped file name.
    assert "\"Queue/\"+'kboat-queue-'+Date.now()" in out
    # The Obsidian URI write, opened silently so capture does not surface a note.
    assert "obsidian://new?vault=" in out
    assert "&silent=true" in out
    # Title and URL are read in the browser at click time, not interpolated here,
    # and assembled into the `[title](url)` markdown-link body with a trailing newline.
    assert "document.title" in out and "location.href" in out
    assert "'['+t+']('+u+')\\n'" in out


def test_empty_folder_drops_the_prefix() -> None:
    # An empty folder files the note at the vault root: no leading slash.
    out = build_bookmarklet("V", "")
    assert "\"\"+'kboat-queue-'+Date.now()" in out


def test_nested_folder_keeps_one_trailing_slash() -> None:
    assert '"a/b/"+\'kboat-queue-' in build_bookmarklet("V", "a/b")
    # Surrounding slashes are normalised to a single trailing separator.
    assert '"a/b/"+\'kboat-queue-' in build_bookmarklet("V", "/a/b/")


def test_vault_name_with_quotes_is_escaped() -> None:
    # A vault name containing a double quote must not break the JS string literal.
    out = build_bookmarklet('My "Vault"', "Queue")
    assert 'encodeURIComponent("My \\"Vault\\"")' in out


def test_cli_prints_bookmarklet(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--vault", "K-Boat", "--folder", "Queue"]) == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith("javascript:") and 'encodeURIComponent("K-Boat")' in line


def test_cli_folder_defaults_to_queue(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--vault", "K-Boat"]) == 0
    assert '"Queue/"+\'kboat-queue-' in capsys.readouterr().out


def test_cli_vault_defaults_to_vault_path_basename(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", "/Users/me/Documents/K-Boat/")
    assert main([]) == 0
    assert 'encodeURIComponent("K-Boat")' in capsys.readouterr().out


def test_cli_errors_without_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    with pytest.raises(SystemExit) as exc:  # argparse parser.error exits with code 2
        main([])
    assert exc.value.code == 2
