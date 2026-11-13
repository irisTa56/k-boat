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
- `assess --slug s --body-chars n --act-early yes|no` — record what the body read
  of one candidate found on its source note (kboat-notes "Daily pick"), under the
  vault lock like `set`.

Both default the vault to `$OBSIDIAN_VAULT_PATH` and accept `--today` for
reproducibility, mirroring `kboat-lifecycle`.

A required input either one could not read — `Sources/` absent, not a directory, or
refused, or for `candidates` a `Questions.md` it could not read — is an `anomalies`
entry under its own name and exit 1, with the report still printed
(`kboat-vault-conventions` "Vault preconditions").
"""

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
    scan_required_dir,
    vault_path,
)
from kboat.io_utils import atomic_write_text
from kboat.lock import VaultLockedError, VaultLockUnavailableError, vault_lock
from kboat.schema import DAILY_DIR, DIR_BY_TYPE, QUESTIONS_FILE
from kboat.write import BadInputError, filename_slug

from .candidates import candidate_from, is_active_web
from .dailynotes import DEFAULT_LOOKBACK_DAYS, extract_daily_notes
from .notes import NOTE_READ_ERRORS, Value, parse_frontmatter, set_assessment, set_picked
from .questions import QuestionsUnreadableError, extract_questions


def _load_sources(
    vault: Path,
) -> tuple[list[tuple[str, str, dict[str, Value]]], list[dict[str, str]], bool]:
    """Parse every `Sources/*.md` note. Returns `(notes, anomalies, unread)` where each
    note is `(slug, rel_path, frontmatter)` and `unread` is whether `Sources/` itself
    could not be read."""
    notes: list[tuple[str, str, dict[str, Value]]] = []
    found, anomalies, unread = scan_required_dir(vault, DIR_BY_TYPE["source"])
    for path in found:
        rel = path.relative_to(vault).as_posix()
        try:
            fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        except NOTE_READ_ERRORS as exc:
            anomalies.append({"path": rel, "error": str(exc)})
            continue
        notes.append((path.stem, rel, fm))
    return notes, anomalies, unread


def _cmd_candidates(vault: Path, today: date, lookback_days: int) -> tuple[dict[str, object], bool]:
    """The candidates report, and whether a required input — `Sources/` or
    `Questions.md` — could not be read. `Daily/` never sets it: the pick degrades
    over its Daily notes by design, so what could not be read there is only reported.
    """
    notes, anomalies, unread = _load_sources(vault)
    candidates = [
        candidate_from(slug, rel, fm).to_json() for slug, rel, fm in notes if is_active_web(fm)
    ]
    days, unreadable_days = extract_daily_notes(vault / DAILY_DIR, today, lookback_days)
    daily_notes = [{"date": dn.date, "body": dn.body} for dn in days]
    anomalies.extend(unreadable_days)
    try:
        questions = [
            {"rank": q.rank, "question": q.question, "note": q.note}
            for q in extract_questions(vault / QUESTIONS_FILE)
        ]
    except QuestionsUnreadableError as exc:
        questions = []
        anomalies.append({"path": QUESTIONS_FILE, "error": str(exc)})
        unread = True
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
    }, unread


def _cmd_set(vault: Path, slugs: list[str]) -> tuple[dict[str, object], bool]:
    chosen = set(slugs)
    notes, anomalies, unread = _load_sources(vault)
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
        except NOTE_READ_ERRORS as exc:
            anomalies.append({"path": rel, "error": f"picked write failed: {exc}"})
    return {
        "vault": str(vault),
        "requested": sorted(chosen),
        "picked": sorted(picked),
        # A slug is missing only from a `Sources/` that was read: an unread one
        # holds nothing this pass could look for, and `missing` is read as a slug
        # the ranker named that the vault does not hold.
        "missing": [] if unread else sorted(chosen - present),
        "reset": reset,
        "anomalies": anomalies,
    }, unread


def _cmd_assess(vault: Path, slug: str, body_chars: int, act_early: bool) -> dict[str, object]:
    rel = f"{DIR_BY_TYPE['source']}/{slug}.md"
    path = vault / rel
    anomalies: list[dict[str, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
        new_text = set_assessment(text, body_chars, act_early)
        if new_text != text:
            atomic_write_text(path, new_text)
    except NOTE_READ_ERRORS as exc:
        anomalies.append({"path": rel, "error": f"assessment write failed: {exc}"})
    return {"vault": str(vault), "slug": slug, "assessed": not anomalies, "anomalies": anomalies}


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

    p_assess = sub.add_parser(
        "assess", help="Record one candidate's body read (length and act-early) on its source."
    )
    p_assess.add_argument("--slug", required=True, help="The source read.")
    p_assess.add_argument(
        "--body-chars", type=int, required=True, help="The body's character count."
    )
    p_assess.add_argument(
        "--act-early",
        choices=("yes", "no"),
        required=True,
        help="Whether the body holds something worth acting on now.",
    )

    args = parser.parse_args(argv)

    vault = vault_path(parser, args)

    # No gate on `Sources/` ahead of the scan, for the reason `kboat-lifecycle`
    # gives at its own: the scan reports absent, not-a-directory and refused
    # itself, where an `is_dir()` answered "no such directory" for all three and
    # ended the run with no JSON.
    if args.command == "candidates":
        today = date.fromisoformat(args.today)
        if args.lookback_days < 0:
            parser.error(f"--lookback-days must be >= 0, got {args.lookback_days}")
        output, unread = _cmd_candidates(vault, today, args.lookback_days)
    elif args.command == "assess":
        # The slug is joined under `Sources/`, so the writer's own check of a
        # filename keeps one naming a path from writing anywhere else.
        try:
            filename_slug(args.slug)
        except BadInputError as exc:
            parser.error(str(exc))
        if args.body_chars < 0:
            parser.error(f"--body-chars must be >= 0, got {args.body_chars}")
        try:
            with vault_lock(vault):
                output = _cmd_assess(vault, args.slug, args.body_chars, args.act_early == "yes")
        except VaultLockedError as exc:
            return emit_locked(exc)
        except VaultLockUnavailableError as exc:
            return emit_lock_unavailable(exc)
        # A note it could not write is reported, not failed, as `set` reports one:
        # exit 1 is the required-input family's, which a single note is not.
        unread = False
    else:
        slugs = [s.strip() for s in args.slugs.split(",") if s.strip()]
        try:
            with vault_lock(vault):
                output, unread = _cmd_set(vault, slugs)
        except VaultLockedError as exc:
            return emit_locked(exc)
        except VaultLockUnavailableError as exc:
            return emit_lock_unavailable(exc)

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if unread else 0


if __name__ == "__main__":
    raise SystemExit(main())
