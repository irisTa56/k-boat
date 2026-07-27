"""CLI entry point: `kboat-validate`.

Checks every note in the vault (`Sources/`, `Kindles/`, `Repos/`, `Feeds/`)
against its schema (`kboat.schema`) and prints the violations as JSON. Read-only — it never
writes. Exit 0 by default (report-only, for the routine's run summary); with
`--strict`, exit 1 when any violation is found.

`--stats` adds the backlog-health counts (`kboat.validate.stats`) to the same
report. They describe how the backlog is moving rather than whether a note is
well-formed, so they never change the exit code — `--strict` still keys on
violations alone.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from kboat.cli import add_today_argument, add_vault_argument, vault_path
from kboat.frontmatter import FrontmatterError, parse_frontmatter
from kboat.lifecycle.core import Kindle, Source
from kboat.schema import DIR_BY_TYPE

from .core import Violation, check_note
from .stats import compute_stats


def _validate_vault(
    vault: Path,
) -> tuple[dict[str, int], list[Violation], list[Source], list[Kindle]]:
    """The per-type note counts, every violation, and the notes the stats need.

    The sources and Kindle books come back from this one pass rather than a second
    scan, so the stats describe exactly the notes that were validated.

    Built whether or not `--stats` asked for them, so the walk has one behaviour.
    """
    checked: dict[str, int] = {}
    violations: list[Violation] = []
    sources: list[Source] = []
    kindles: list[Kindle] = []
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
            # The stats describe the lifecycle's work sets, and the lifecycle
            # loads a source by its declared `type`, not by the folder it sits in
            # — a misfiled note is an anomaly there, so it must not become a
            # phantom count here that no run can ever drain. Validation still
            # reports it: the wrong `type` is a `bad_enum` against this schema.
            if note_type == "source" and fm.get("type") == "source":
                sources.append(Source.from_frontmatter(path.stem, rel, fm))
            elif note_type == "kindle" and fm.get("type") == "kindle":
                kindles.append(Kindle.from_frontmatter(path.stem, rel, fm))
        checked[note_type] = count
    return checked, violations, sources, kindles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-validate",
        description="Check every vault note's frontmatter against its schema (read-only).",
    )
    add_vault_argument(parser)
    # Visible: it decides the ages `--stats` reports, so the shared flag's
    # "reproducibility" is a real use here rather than only a test hook.
    add_today_argument(parser)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any violation is found (default: report-only, exit 0).",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Also report the backlog-health counts as of --today (never affects the exit code).",
    )
    args = parser.parse_args(argv)

    vault = vault_path(parser, args)

    checked, violations, sources, kindles = _validate_vault(vault)
    by_code = Counter(v.code for v in violations)
    output: dict[str, object] = {
        "vault": str(vault),
        "checked": checked,
        "violations": [v.to_json() for v in violations],
        "counts": {"total": len(violations), "by_code": dict(sorted(by_code.items()))},
    }
    if args.stats:
        output["stats"] = compute_stats(sources, kindles, date.fromisoformat(args.today)).to_json()
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if (args.strict and violations) else 0


if __name__ == "__main__":
    raise SystemExit(main())
