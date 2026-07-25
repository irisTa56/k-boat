"""`kboat-repos write`: write a repo note from gather + classification.

Reads one JSON object on stdin — a `gather` record (its `slug`/`url`/`title`/
`fields`) augmented by the skill with the judged `role`, `domain`, `summary` —
and writes `Repos/<slug>.md` through `kboat.write.upsert` under the `REPO`
schema. Everything mechanical (field order, YAML quoting, de-dup by `url`, body
preservation, the date stamps) belongs to that shared writer, so this module is
only the translation between the record shape `gather` speaks and the
`{slug, fields}` one `upsert` speaks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from kboat.frontmatter import FrontmatterError
from kboat.schema import REPO
from kboat.write import upsert

REQUIRED = ("slug", "url", "title", "fields", "role", "domain", "summary")

# Schema fields the record may not carry, because they are not its to know:
# `reading` is the human's checkbox and the stamps are the schema's. `upsert`
# preserves a field the write leaves alone and overwrites one it is given, so
# dropping these is what keeps them the human's and the schema's.
_NOT_FROM_THE_RECORD = frozenset({"reading"} | {f.name for f in REPO.fields if f.stamp})


def write_note(record: dict, vault: Path, *, today_iso: str) -> dict[str, object]:
    """Create or update `Repos/<slug>.md` from a gather + classification record.

    `record["fields"]` is `gather`'s GitHub-derived block; the identity and the
    judgement layer arrive as top-level keys. Only fields the schema knows pass
    through, so a key the classifier invents cannot reach the frontmatter —
    `upsert` would otherwise append it rather than drop it.
    """
    fields: dict[str, object] = {
        key: value
        for key, value in record["fields"].items()
        if REPO.get(key) is not None and key not in _NOT_FROM_THE_RECORD
    }
    fields.update(
        {
            "type": "repo",
            "title": record["title"],
            "url": record["url"],
            "role": record["role"],
            "domain": record["domain"],
            "summary": record["summary"],
        }
    )
    return upsert(REPO, vault, {"slug": record["slug"], "fields": fields}, today=today_iso)


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
    if not isinstance(record, dict):
        sys.stderr.write("record must be a JSON object\n")
        return 2
    missing = [k for k in REQUIRED if k not in record]
    if missing:
        sys.stderr.write(f"record is missing required keys: {', '.join(missing)}\n")
        return 2
    if not isinstance(record["fields"], dict):
        sys.stderr.write("record 'fields' must be a JSON object\n")
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
