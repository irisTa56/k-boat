"""`kboat-repos write`: assemble and write a repo note from gather + classification.

Reads one JSON object on stdin — a `gather` record (its `slug`/`url`/`title`/
`fields`) augmented by the skill with the judged `role`, `domain`, `summary` —
and writes `Repos/<slug>.md` via `build_repo_note`. Frontmatter order, YAML
quoting, de-dup, and `## Notes` body preservation are guaranteed by the package,
so the agent never hand-writes frontmatter (which would risk a colon-bearing
description producing invalid YAML, or field-order drift from the schema).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from kboat.io_utils import atomic_write_text

from .notes import (
    FrontmatterError,
    body_after_frontmatter,
    build_repo_note,
    parse_frontmatter,
    split_notes_section,
)

REQUIRED = ("slug", "url", "title", "fields", "role", "domain", "summary")


def _existing_notes_body(text: str) -> str:
    """The content under `## Notes`, or the whole body when there is no heading."""
    head, notes = split_notes_section(body_after_frontmatter(text))
    return head if notes is None else notes


def write_note(record: dict, vault: Path, *, today_iso: str) -> dict:
    slug = record["slug"]
    url = record["url"]
    fields_in = record["fields"]
    path = vault / "Repos" / f"{slug}.md"

    body = ""
    reading = False
    added_date = today_iso
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(existing)
        existing_url = fm.get("url")
        if isinstance(existing_url, str) and existing_url != url:
            # Same slug, different repo — a 48-bit hash collision. Never overwrite.
            return {"status": "collision", "slug": slug, "url": url, "existing_url": existing_url}
        body = _existing_notes_body(existing)  # preserve the human-edited body
        reading = fm.get("reading") is True  # preserve the human's reading checkbox
        if isinstance(fm.get("added_date"), str) and fm["added_date"]:
            added_date = fm["added_date"]  # keep the original ingest date on update

    fields: dict[str, object] = {
        "type": "repo",
        "title": record["title"],
        "url": url,
        "homepage": fields_in.get("homepage", ""),
        "reading": reading,
        "description": fields_in.get("description", ""),
        "language": fields_in.get("language", []),
        "topics": fields_in.get("topics", []),
        "stars": fields_in.get("stars", 0),
        "archived": bool(fields_in.get("archived")),
        "created_at": fields_in.get("created_at", ""),
        "last_commit": fields_in.get("last_commit", ""),
        "license": fields_in.get("license", ""),
        "role": record["role"],
        "domain": record["domain"],
        "summary": record["summary"],
        "status": fields_in.get("status", "unknown"),
        "added_date": added_date,
        "refreshed_date": today_iso,
    }
    created = not path.exists()
    atomic_write_text(path, build_repo_note(fields, notes_body=body))
    return {"status": "created" if created else "updated", "slug": slug, "path": f"Repos/{slug}.md"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-repos write",
        description="Write a Repos/<slug>.md note from a gather record + classification (JSON on stdin).",
    )
    parser.add_argument(
        "--vault",
        default=os.environ.get("OBSIDIAN_VAULT_PATH"),
        help="Obsidian vault root (defaults to $OBSIDIAN_VAULT_PATH).",
    )
    parser.add_argument(
        "--today",
        default=date.today().isoformat(),
        help="Override today's date (YYYY-MM-DD); for testing and reproducibility.",
    )
    args = parser.parse_args(argv)

    if not args.vault:
        parser.error("no vault: pass --vault or set OBSIDIAN_VAULT_PATH")
    try:
        date.fromisoformat(args.today)
    except ValueError:
        parser.error(f"--today must be YYYY-MM-DD, got {args.today!r}")

    try:
        record = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"stdin is not valid JSON: {e}\n")
        return 2
    missing = [k for k in REQUIRED if k not in record]
    if missing:
        sys.stderr.write(f"record is missing required keys: {', '.join(missing)}\n")
        return 2

    try:
        result = write_note(record, Path(args.vault).expanduser(), today_iso=args.today)
    except (FrontmatterError, OSError) as e:
        sys.stderr.write(f"write failed: {e}\n")
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if result["status"] == "collision" else 0


if __name__ == "__main__":
    raise SystemExit(main())
