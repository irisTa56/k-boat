"""CLI entry point: `kboat-pick`.

Two deterministic subcommands behind the daily pick (the relevance ranking is the
LLM step in `kboat-recall`; this tool does the mechanical I/O around it):

- `candidates` — read the Daily-note `## 明日への問い` questions (newest-first) and
  the active web inbox, and print both as JSON for the ranker.
- `set --slugs a,b` — reset `picked` to false on every source, then set it true on
  the chosen slugs (at most two). An empty `--slugs` just clears the spotlight.

Both default the vault to `$OBSIDIAN_VAULT_PATH` and accept `--today` for
reproducibility, mirroring `kboat-lifecycle`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from .candidates import candidate_from, is_active_web
from .dailynotes import extract_questions
from .notes import FrontmatterError, Value, parse_frontmatter, set_picked


def _load_sources(
    vault: Path,
) -> tuple[list[tuple[str, str, dict[str, Value]]], list[dict[str, str]]]:
    """Parse every `Sources/*.md` note. Returns `(notes, anomalies)` where each
    note is `(slug, rel_path, frontmatter)`."""
    notes: list[tuple[str, str, dict[str, Value]]] = []
    anomalies: list[dict[str, str]] = []
    for path in sorted((vault / "Sources").glob("*.md")):
        rel = path.relative_to(vault).as_posix()
        try:
            fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        except (FrontmatterError, OSError) as exc:
            anomalies.append({"path": rel, "error": str(exc)})
            continue
        notes.append((path.stem, rel, fm))
    return notes, anomalies


def _cmd_candidates(vault: Path, today: date) -> dict[str, object]:
    notes, anomalies = _load_sources(vault)
    candidates = [
        candidate_from(slug, rel, fm).to_json() for slug, rel, fm in notes if is_active_web(fm)
    ]
    questions = [
        {"date": dq.date, "items": dq.items} for dq in extract_questions(vault / "Daily", today)
    ]
    return {
        "today": today.isoformat(),
        "vault": str(vault),
        "questions": questions,
        "candidates": candidates,
        "counts": {
            "candidates_total": len(candidates),
            "question_days": len(questions),
            "question_items": sum(len(q["items"]) for q in questions),
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
                path.write_text(new_text, encoding="utf-8")
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
        description="Daily-pick mechanics: gather questions + web candidates, and set the picked flag.",
    )
    parser.add_argument(
        "--vault",
        default=os.environ.get("OBSIDIAN_VAULT_PATH"),
        help="Obsidian vault root (defaults to $OBSIDIAN_VAULT_PATH).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_cand = sub.add_parser(
        "candidates", help="Print Daily-note questions and the active web inbox as JSON."
    )
    p_cand.add_argument(
        "--today",
        default=date.today().isoformat(),
        help="Override today's date (YYYY-MM-DD); for testing and reproducibility.",
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

    if not args.vault:
        parser.error("no vault: pass --vault or set OBSIDIAN_VAULT_PATH")
    vault = Path(args.vault).expanduser()
    if not (vault / "Sources").is_dir():
        parser.error(f"no Sources/ directory under vault: {vault / 'Sources'}")

    if args.command == "candidates":
        try:
            today = date.fromisoformat(args.today)
        except ValueError:
            parser.error(f"--today must be YYYY-MM-DD, got {args.today!r}")
        output = _cmd_candidates(vault, today)
    else:
        slugs = [s.strip() for s in args.slugs.split(",") if s.strip()]
        output = _cmd_set(vault, slugs)

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
