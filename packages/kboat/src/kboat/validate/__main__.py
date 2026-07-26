"""CLI entry point: `kboat-validate`.

Checks every note in the vault (`Sources/`, `Kindles/`, `Repos/`, `Feeds/`)
against its schema (`kboat.schema`) and prints the violations as JSON. Read-only — it never
writes. Exit 0 by default (report-only, for the routine's run summary); with
`--strict`, exit 1 when any violation is found.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from kboat.cli import add_vault_argument, vault_path
from kboat.frontmatter import FrontmatterError, parse_frontmatter
from kboat.schema import DIR_BY_TYPE

from .core import Violation, check_note


def _validate_vault(vault: Path) -> tuple[dict[str, int], list[Violation]]:
    checked: dict[str, int] = {}
    violations: list[Violation] = []
    for note_type, subdir in DIR_BY_TYPE.items():
        directory = vault / subdir
        count = 0
        for path in sorted(directory.glob("*.md")) if directory.is_dir() else []:
            rel = path.relative_to(vault).as_posix()
            count += 1
            try:
                fm = parse_frontmatter(path.read_text(encoding="utf-8"))
            except (FrontmatterError, OSError) as exc:
                violations.append(Violation(rel, "_frontmatter", "parse_error", str(exc)))
                continue
            violations.extend(check_note(note_type, fm, rel))
        checked[note_type] = count
    return checked, violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-validate",
        description="Check every vault note's frontmatter against its schema (read-only).",
    )
    add_vault_argument(parser)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any violation is found (default: report-only, exit 0).",
    )
    args = parser.parse_args(argv)

    vault = vault_path(parser, args)

    checked, violations = _validate_vault(vault)
    by_code = Counter(v.code for v in violations)
    output = {
        "vault": str(vault),
        "checked": checked,
        "violations": [v.to_json() for v in violations],
        "counts": {"total": len(violations), "by_code": dict(sorted(by_code.items()))},
    }
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if (args.strict and violations) else 0


if __name__ == "__main__":
    raise SystemExit(main())
