"""End-to-end tests for the `kboat-note` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kboat.note.__main__ import main


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    for sub in ("Sources", "Kindles", "Repos"):
        (tmp_path / sub).mkdir()
    return tmp_path


def _run(argv: list[str], stdin: str, monkeypatch: pytest.MonkeyPatch) -> int:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return main(argv)


def test_write_source_creates(
    vault: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = json.dumps(
        {
            "slug": "s1",
            "fields": {
                "type": "source",
                "title": "T",
                "url": "https://x",
                "source_type": "web_page",
            },
        }
    )
    assert (
        _run(
            ["write", "--type", "source", "--vault", str(vault), "--today", "2026-06-13"],
            rec,
            monkeypatch,
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "created"
    assert (vault / "Sources" / "s1.md").exists()


def test_collision_exits_nonzero(
    vault: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    a = json.dumps(
        {
            "slug": "s1",
            "fields": {
                "type": "source",
                "url": "https://a",
                "source_type": "web_page",
                "title": "A",
            },
        }
    )
    _run(["write", "--type", "source", "--vault", str(vault)], a, monkeypatch)
    capsys.readouterr()
    b = json.dumps({"slug": "s1", "fields": {"type": "source", "url": "https://b", "title": "B"}})
    assert _run(["write", "--type", "source", "--vault", str(vault)], b, monkeypatch) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "collision"


def test_bad_input_and_usage(
    vault: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The stderr line says which refusal it was, so the agent knows what to fix.
    assert _run(["write", "--type", "source", "--vault", str(vault)], "not json", monkeypatch) == 2
    assert "not valid JSON" in capsys.readouterr().err
    assert (
        _run(["write", "--type", "source", "--vault", str(vault)], '{"no":"slug"}', monkeypatch)
        == 2
    )
    assert "'slug' key" in capsys.readouterr().err
    assert main([]) == 0  # bare usage
    assert main(["bogus"]) == 2  # unknown subcommand
