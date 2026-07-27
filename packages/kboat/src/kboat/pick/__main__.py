"""CLI entry point: `kboat-pick`.

Two deterministic subcommands behind the daily pick (the relevance ranking is the
LLM step in `kboat-recall`; this tool does the mechanical I/O around it):

- `candidates` — read the two interest signals (the recent Daily-note bodies,
  newest-first within a `--lookback-days` window, and the open-questions backlog
  from `Questions.md`) and the active web inbox, and print them as JSON for the
  ranker, which infers the human's current interests from the notes and questions.
- `set --slugs a,b` — reset `picked` to false on every source, then set it true on
  the chosen slugs (at most two). An empty `--slugs` just clears the spotlight.
  It is the writing half, so it holds the vault lock and reports a `locked`
  record rather than racing another run; `candidates` reads only and takes none.

Both default the vault to `$OBSIDIAN_VAULT_PATH` and accept `--today` for
reproducibility, mirroring `kboat-lifecycle`.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from kboat.cli import (
    add_today_argument,
    add_vault_argument,
    emit_lock_unavailable,
    emit_locked,
    vault_path,
)
from kboat.io_utils import atomic_write_text
from kboat.lock import VaultLockedError, VaultLockUnavailableError, vault_lock
from kboat.schema import DAILY_DIR, DIR_BY_TYPE, QUESTIONS_FILE

from .candidates import candidate_from, is_active_web
from .dailynotes import DEFAULT_LOOKBACK_DAYS, extract_daily_notes
from .notes import FrontmatterError, Value, parse_frontmatter, set_picked
from .questions import extract_questions


def _load_sources(
    vault: Path,
) -> tuple[list[tuple[str, str, dict[str, Value]]], list[dict[str, str]]]:
    """Parse every `Sources/*.md` note. Returns `(notes, anomalies)` where each
    note is `(slug, rel_path, frontmatter)`."""
    notes: list[tuple[str, str, dict[str, Value]]] = []
    anomalies: list[dict[str, str]] = []
    for path in sorted((vault / DIR_BY_TYPE["source"]).glob("*.md")):
        rel = path.relative_to(vault).as_posix()
        try:
            fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        except (FrontmatterError, OSError) as exc:
            anomalies.append({"path": rel, "error": str(exc)})
            continue
        notes.append((path.stem, rel, fm))
    return notes, anomalies


def _cmd_candidates(vault: Path, today: date, lookback_days: int) -> dict[str, object]:
    notes, anomalies = _load_sources(vault)
    candidates = [
        candidate_from(slug, rel, fm).to_json() for slug, rel, fm in notes if is_active_web(fm)
    ]
    daily_notes = [
        {"date": dn.date, "body": dn.body}
        for dn in extract_daily_notes(vault / DAILY_DIR, today, lookback_days)
    ]
    questions = [
        {"rank": q.rank, "question": q.question, "note": q.note}
        for q in extract_questions(vault / QUESTIONS_FILE)
    ]
    return {
        "today": today.isoformat(),
        "vault": str(vault),
        "lookback_days": lookback_days,
        "daily_notes": daily_notes,
        "questions": questions,
        "candidates": candidates,
        "counts": {
            "candidates_total": len(candidates),
            "daily_note_days": len(daily_notes),
            "questions_total": len(questions),
        },
        "anomalies": anomalies,
    }


def _cmd_set(vault: Path, slugs: list[str]) -> dict[str, object]:
    chosen = set(slugs)
    notes, anomalies = _load_sources(vault)
    present = {slug for slug, _, _ in notes}
    picked: list[str] = []
    reset = 0
    for slug, rel, _ in notes:
        want = slug in chosen
        path = vault / rel
        try:
            text = path.read_text(encoding="utf-8")
            new_text = set_picked(text, want)
            if new_text != text:
                atomic_write_text(path, new_text)
            if want:
                picked.append(slug)
            else:
                reset += 1
        except (FrontmatterError, OSError) as exc:
            anomalies.append({"path": rel, "error": f"picked write failed: {exc}"})
    return {
        "vault": str(vault),
        "requested": sorted(chosen),
        "picked": sorted(picked),
        "missing": sorted(chosen - present),
        "reset": reset,
        "anomalies": anomalies,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-pick",
        description="Daily-pick mechanics: gather the interest signals (recent Daily notes + open-questions backlog) and web candidates, and set the picked flag.",
    )
    add_vault_argument(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    p_cand = sub.add_parser(
        "candidates",
        help="Print the interest signals (Daily-note bodies + open-questions backlog) and the active web inbox as JSON.",
    )
    add_today_argument(p_cand)
    p_cand.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=(
            "Days of Daily notes to consider, counting back from today, "
            f"inclusive (default {DEFAULT_LOOKBACK_DAYS}). Older notes are out of scope."
        ),
    )

    p_set = sub.add_parser(
        "set", help="Reset picked on every source, then set it on the chosen slugs."
    )
    p_set.add_argument(
        "--slugs",
        default="",
        help="Comma-separated slugs to mark picked (at most two). Empty clears all picks.",
    )

    args = parser.parse_args(argv)

    vault = vault_path(parser, args)
    sources_dir = vault / DIR_BY_TYPE["source"]
    if not sources_dir.is_dir():
        parser.error(f"no {DIR_BY_TYPE['source']}/ directory under vault: {sources_dir}")

    if args.command == "candidates":
        today = date.fromisoformat(args.today)
        if args.lookback_days < 0:
            parser.error(f"--lookback-days must be >= 0, got {args.lookback_days}")
        output = _cmd_candidates(vault, today, args.lookback_days)
    else:
        slugs = [s.strip() for s in args.slugs.split(",") if s.strip()]
        try:
            with vault_lock(vault):
                output = _cmd_set(vault, slugs)
        except VaultLockedError as exc:
            return emit_locked(exc)
        except VaultLockUnavailableError as exc:
            return emit_lock_unavailable(exc)

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
