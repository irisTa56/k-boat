"""CLI entry point: `kboat-validate`.

Checks every note in the vault (`Sources/`, `Kindles/`, `Repos/`, `Feeds/`)
against its schema (`kboat.schema`) and prints the violations as JSON. Read-only — it never
writes. Exit 0 by default (report-only, for the routine's run summary); with
`--strict`, exit 1 when any violation is found.

`--stats` adds the backlog-health counts (`kboat.validate.stats`) to the same
report. They describe how the backlog is moving rather than whether a note is
well-formed, so they never change the exit code — `--strict` still keys on
violations alone. The queue's counts come from a listing of `Queue/`, whose
captures are not schema'd notes and are not validated.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from kboat.cli import add_today_argument, add_vault_argument, vault_path
from kboat.frontmatter import NOTE_READ_ERRORS, Value, parse_frontmatter
from kboat.io_utils import list_note_dir
from kboat.lifecycle.core import Kindle, Source
from kboat.schema import DIR_BY_TYPE, QUEUE_DIR

from .core import Violation, check_note, check_repeated_keys
from .stats import compute_stats


@dataclass
class _Backlog:
    """What the stats are computed over, gathered by the validation walk."""

    sources: list[Source] = field(default_factory=list)
    kindles: list[Kindle] = field(default_factory=list)
    repo_refreshed: list[Value] = field(default_factory=list)
    captures: list[str] = field(default_factory=list)


def _validate_vault(vault: Path) -> tuple[dict[str, int], list[Violation], _Backlog]:
    """The per-type note counts, every violation, and the notes the stats need.

    The notes come back from this one pass rather than a second scan, so the
    stats describe exactly the notes that were validated.

    Built whether or not `--stats` asked for them, so the walk has one behaviour.
    """
    checked: dict[str, int] = {}
    violations: list[Violation] = []
    backlog = _Backlog()
    for note_type, subdir in DIR_BY_TYPE.items():
        directory = vault / subdir
        count = 0
        # `checked` counts what this pass could **list**; the stats count what it
        # could read, so a note that will not parse is in the first and not the
        # second. What the two entries below buy is not a truer count either: it is
        # that a vault this pass could not read whole says so in the same report,
        # instead of handing back a short backlog that reads like a healthy one. A
        # directory the OS will not give up and a note iCloud evicted are the same
        # silence from two directions, and `*.md` alone sees neither.
        try:
            found, placeholders = list_note_dir(directory)
        except OSError as exc:
            violations.append(Violation(subdir, "_dir", "unreadable_dir", str(exc)))
            checked[note_type] = count
            continue
        for placeholder in placeholders:
            violations.append(
                Violation(
                    placeholder.relative_to(vault).as_posix(),
                    "_file",
                    "icloud_placeholder",
                    "evicted to an iCloud placeholder, so the note could not be read",
                )
            )
        for path in found:
            rel = path.relative_to(vault).as_posix()
            count += 1
            try:
                text = path.read_text(encoding="utf-8")
                fm = parse_frontmatter(text)
            except NOTE_READ_ERRORS as exc:
                violations.append(Violation(rel, "_frontmatter", "parse_error", str(exc)))
                continue
            violations.extend(check_note(note_type, fm, rel))
            violations.extend(check_repeated_keys(text, rel))
            # The stats describe the lifecycle's work sets, and the lifecycle
            # loads a source by its declared `type`, not by the folder it sits in
            # — a misfiled note is an anomaly there, so it must not become a
            # phantom count here that no run can ever drain. Validation still
            # reports it: the wrong `type` is a `bad_enum` against this schema.
            if note_type == "source" and fm.get("type") == "source":
                backlog.sources.append(Source.from_frontmatter(path.stem, rel, fm))
            elif note_type == "kindle" and fm.get("type") == "kindle":
                backlog.kindles.append(Kindle.from_frontmatter(path.stem, rel, fm))
            elif note_type == "repo" and fm.get("type") == "repo":
                # The refresh reads a repo note by its `type` too.
                backlog.repo_refreshed.append(fm.get("refreshed_date"))
        checked[note_type] = count
    # The captures ingest drains, by name: the name is the capture's timestamp, and
    # nothing in a capture's body is read here. Refused rather than absent is the
    # same silence as a note directory's, so it is reported the same way; an
    # absent `Queue/` is `kboat-doctor`'s to report, and reads here as empty.
    try:
        found, _ = list_note_dir(vault / QUEUE_DIR)
    except OSError as exc:
        violations.append(Violation(QUEUE_DIR, "_dir", "unreadable_dir", str(exc)))
    else:
        backlog.captures = [path.name for path in found]
    return checked, violations, backlog


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

    checked, violations, backlog = _validate_vault(vault)
    by_code = Counter(v.code for v in violations)
    output: dict[str, object] = {
        "vault": str(vault),
        "checked": checked,
        "violations": [v.to_json() for v in violations],
        "counts": {"total": len(violations), "by_code": dict(sorted(by_code.items()))},
    }
    if args.stats:
        output["stats"] = compute_stats(
            backlog.sources,
            backlog.kindles,
            date.fromisoformat(args.today),
            repo_refreshed=backlog.repo_refreshed,
            captures=backlog.captures,
        ).to_json()
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if (args.strict and violations) else 0


if __name__ == "__main__":
    raise SystemExit(main())
