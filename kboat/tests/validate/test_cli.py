"""End-to-end tests for the `kboat-validate` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kboat.validate.__main__ import main

VALID_SOURCE = """\
---
type: source
title: Clean
reading_link: https://example.com/a
reading: false
distill: false
keep: false
dismiss: false
source_type: web_page
url: https://example.com/a
summary: ok
topics:
  - x
added_date: 2026-06-01
filed_date:
distilled_date:
blocked: false
picked: false
notebooklm_id: id
notebooklm_url: https://n
tags: []
---
"""

# Missing the required `picked` boolean, and a bad `source_type`.
BAD_SOURCE = VALID_SOURCE.replace("picked: false\n", "").replace(
    "source_type: web_page", "source_type: podcast"
)


def _vault(tmp_path: Path, **notes: str) -> Path:
    for sub in ("Sources", "Kindles", "Repos"):
        (tmp_path / sub).mkdir()
    for name, text in notes.items():
        (tmp_path / "Sources" / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_clean_vault_reports_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path, **{"a.md": VALID_SOURCE})
    assert main(["--vault", str(vault)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["checked"] == {"source": 1, "kindle": 0, "repo": 0}
    assert out["counts"]["total"] == 0


def test_violations_reported_and_strict_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _vault(tmp_path, **{"bad.md": BAD_SOURCE})
    # Default: report-only, exit 0.
    assert main(["--vault", str(vault)]) == 0
    out = json.loads(capsys.readouterr().out)
    codes = {v["code"] for v in out["violations"]}
    assert "missing_field" in codes  # picked
    assert "bad_enum" in codes  # source_type
    assert out["counts"]["total"] >= 2

    # --strict: same findings, non-zero exit.
    assert main(["--vault", str(vault), "--strict"]) == 1


def test_parse_error_is_a_violation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _vault(tmp_path, **{"broken.md": "no frontmatter here\n"})
    main(["--vault", str(vault)])
    out = json.loads(capsys.readouterr().out)
    assert any(v["code"] == "parse_error" for v in out["violations"])
