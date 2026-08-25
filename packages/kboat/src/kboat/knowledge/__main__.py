"""CLI entry point: `kboat-knowledge`.

Checks every concept note under the knowledge root (`concepts/`) against the
shape `kboat-notes` defines, and prints the findings as JSON. Read-only — it
never writes. Exit 0 by default (report-only, the way `kboat-validate` reports
the vault); with `--strict`, exit 1 when any finding is found.

It reports the count even at zero, because a caller cannot otherwise tell a
clean base from a check that never ran, and this is the only check there is
over the knowledge root.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections import Counter
from pathlib import Path

from kboat.io_utils import list_note_dir

from .core import ShapeFinding, check_concept_note

CONCEPTS = "concepts"
META = "meta"


def _rel(root: Path, path: Path) -> str:
    """`path` as the reader sees it — relative to the knowledge root, POSIX-style."""
    return path.relative_to(root).as_posix()


def _strays(root: Path) -> list[ShapeFinding]:
    """Every `*.md` under the root that the concepts scan does not cover.

    `meta/` is excluded because a hub or vocabulary note is not a concept note
    and follows none of the rules (kboat-notes). Anything else is a note nobody
    checks, which is the one thing this report must not render as silence.
    """
    out: list[ShapeFinding] = []
    for path in sorted(root.rglob("*.md")):
        first = path.relative_to(root).parts[0]
        if first in (CONCEPTS, META):
            continue
        out.append(
            ShapeFinding(
                _rel(root, path),
                "note_outside_concepts",
                f"not under `{CONCEPTS}/`, so no shape check covers it",
            )
        )
    return out


def _knowledge_path(parser: argparse.ArgumentParser, args: argparse.Namespace) -> Path:
    """The knowledge root, or a usage error (exit 2) when neither flag nor env gives one."""
    if not args.knowledge:
        parser.error("no knowledge root: pass --knowledge or set KBOAT_KNOWLEDGE_PATH")
    return Path(args.knowledge).expanduser()


def _concept_dirs(directory: Path) -> list[Path]:
    """`directory` and every directory beneath it, so a subtopic folder is still checked.

    `memory-curate`, which `kboat-curate` delegates its graph work to, proposes
    grouping a crowded folder into subfolders — so a flat scan would quietly
    shrink the moment a human took that advice, and a note it stopped seeing
    would read to `kboat-distill` exactly like a note with nothing wrong.
    """
    out = [directory]
    for path in sorted(directory.rglob("*")):
        if path.is_dir():
            out.append(path)
    return out


def _check_concepts(root: Path) -> tuple[int, list[ShapeFinding]]:
    """The notes checked and every finding, over `<root>/concepts/` and below it.

    `list_note_dir` rather than a glob for the reason it exists: a directory the
    OS refuses to list reads as an empty one through `glob`, and a report of
    "nothing wrong" over a directory that was never read is the failure this
    check is here to prevent.

    A `*.md` anywhere else under the root is reported rather than skipped, for
    the same reason: silence is what a clean base sounds like, so a note this
    scan does not cover has to say so itself.
    """
    directory = root / CONCEPTS
    findings: list[ShapeFinding] = []
    # Before listing, not after: `list_note_dir` answers an absent directory with
    # two empty lists, so without this a root that is simply wrong — a stale
    # `KBOAT_KNOWLEDGE_PATH`, a base that moved — reports zero notes and zero
    # findings, which reads exactly like a clean base. This is the only check
    # there is over the knowledge root and `kboat-distill` branches on it
    # unattended, so a silent nothing is the one answer it must not give.
    # `stat` rather than `is_dir`, which swallows every `OSError` and answers
    # `False` — so a root the OS refuses would be reported as a root that is not
    # there, sending the human to change a path that was right all along. The
    # package's standard is to ask a probe that raises (packages/kboat/CLAUDE.md).
    # The root first, because the two absences are different answers: a root that is
    # not there is a root to fix, while a root that is there without `concepts/` is a
    # base no concept note has reached yet — and the folder is created by the very
    # `write_note` a `base` verdict would stop, so calling that one wrong makes a
    # fresh install distil nothing for ever while reporting the path as the fault.
    try:
        root_mode = root.stat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return 0, [ShapeFinding(".", "no_knowledge_root", f"no directory at {root}")]
    except OSError as exc:
        return 0, [ShapeFinding(".", "unreadable_dir", str(exc))]
    if not stat.S_ISDIR(root_mode):
        return 0, [ShapeFinding(".", "no_knowledge_root", f"not a directory: {root}")]
    # The strays go with it: without `concepts/` they are the only evidence there is,
    # and returning before them made the verdict turn on whether an empty directory
    # exists rather than on anything about the base. A root pointed one level off
    # then read as a fresh install, and the pass started a second, empty base beside
    # the notes it could not see.
    try:
        mode = directory.stat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return 0, [
            ShapeFinding(CONCEPTS, "no_concepts_dir", f"no directory at {directory}"),
            *_strays(root),
        ]
    except OSError as exc:
        return 0, [ShapeFinding(CONCEPTS, "unreadable_dir", str(exc))]
    if not stat.S_ISDIR(mode):
        return 0, [
            ShapeFinding(CONCEPTS, "no_concepts_dir", f"not a directory: {directory}"),
            *_strays(root),
        ]
    found: list[Path] = []
    for concepts_dir in _concept_dirs(directory):
        try:
            notes, placeholders = list_note_dir(concepts_dir)
        except OSError as exc:
            return 0, [ShapeFinding(_rel(root, concepts_dir), "unreadable_dir", str(exc))]
        found.extend(notes)
        for placeholder in placeholders:
            findings.append(
                ShapeFinding(
                    _rel(root, placeholder),
                    "icloud_placeholder",
                    "evicted to an iCloud placeholder, so the note could not be read",
                )
            )
    findings.extend(_strays(root))
    checked = 0
    for path in sorted(found):
        checked += 1
        try:
            text = path.read_text(encoding="utf-8")
        # `UnicodeDecodeError` alongside `OSError`, as `kboat.repos.refresh` catches it
        # and for the same reason: it is a `ValueError`, so a note that is not UTF-8
        # would escape this boundary and take the whole base's report with it — and a
        # caller reading no report at all takes it for a root it could not reach.
        except (OSError, UnicodeDecodeError) as exc:
            findings.append(ShapeFinding(_rel(root, path), "unreadable_note", str(exc)))
            continue
        findings.extend(check_concept_note(text, _rel(root, path)))
    return checked, findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-knowledge",
        description="Check every concept note's shape against kboat-notes (read-only).",
    )
    parser.add_argument(
        "--knowledge",
        default=os.environ.get("KBOAT_KNOWLEDGE_PATH"),
        help="Knowledge root (defaults to $KBOAT_KNOWLEDGE_PATH).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any finding is found (default: report-only, exit 0).",
    )
    args = parser.parse_args(argv)

    root = _knowledge_path(parser, args)
    checked, findings = _check_concepts(root)
    by_code = Counter(f.code for f in findings)
    json.dump(
        {
            "knowledge": str(root),
            "checked": checked,
            "findings": [f.to_json() for f in findings],
            "counts": {"total": len(findings), "by_code": dict(sorted(by_code.items()))},
        },
        sys.stdout,
        ensure_ascii=False,
        indent=2,
    )
    sys.stdout.write("\n")
    return 1 if (args.strict and findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
