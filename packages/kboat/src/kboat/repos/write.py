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
from pathlib import Path

from kboat.cli import (
    BadInputError,
    add_today_argument,
    add_vault_argument,
    require_readable_payload,
    run_write,
    vault_path,
)
from kboat.schema import REPO
from kboat.write import upsert

REQUIRED = ("slug", "url", "title", "fields", "role", "domain", "summary")

# Schema fields the `fields` block may not carry, because they are not its to
# know: `reading` is the human's checkbox and the stamps are the schema's.
# `upsert` preserves a field the write leaves alone and overwrites one it is
# given, so dropping these is what keeps them the human's and the schema's.
_NOT_FROM_THE_RECORD = frozenset({"reading"} | {f.name for f in REPO.fields if f.stamp})


def write_note(record: dict, vault: Path, *, today_iso: str) -> dict[str, object]:
    """Create or update `Repos/<slug>.md` from a gather + classification record.

    `record["fields"]` is `gather`'s GitHub-derived block; the identity and the
    judgement layer arrive as top-level keys. Only a field the block owns passes
    through, so a key the classifier invents cannot reach the frontmatter —
    `upsert` would otherwise append it rather than drop it.

    Everything the block did not own comes back as `dropped_fields` — but only
    on a write that happened, since a refused collision leaves every field
    unset and the collision is the thing to report. The block reaches here by
    way of the skill re-serialising the gather record, so a key that arrives
    misspelled is a field left quietly unset; having decided to discard, saying
    what was discarded is what keeps that visible.
    """
    # The identity and the judgement layer are read off the top level, so a copy
    # of one under `fields` is a duplicate this write would silently overrule.
    top_level: dict[str, object] = {
        "type": "repo",
        "title": record["title"],
        "url": record["url"],
        "role": record["role"],
        "domain": record["domain"],
        "summary": record["summary"],
    }
    fields: dict[str, object] = {}
    dropped: list[str] = []
    for key, value in record["fields"].items():
        if REPO.get(key) is not None and key not in _NOT_FROM_THE_RECORD and key not in top_level:
            fields[key] = value
        else:
            dropped.append(key)
    fields.update(top_level)
    result = upsert(REPO, vault, {"slug": record["slug"], "fields": fields}, today=today_iso)
    if dropped and result["status"] != "collision":
        return {**result, "dropped_fields": dropped}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-repos write",
        description="Write a Repos/<slug>.md note from a gather record + classification (JSON on stdin).",
    )
    add_vault_argument(parser)
    add_today_argument(parser)
    args = parser.parse_args(argv)
    vault = vault_path(parser, args)

    def write(record: dict) -> dict[str, object]:
        missing = [k for k in REQUIRED if k not in record]
        if missing:
            raise BadInputError(f"record is missing required keys: {', '.join(missing)}")
        require_readable_payload(record)
        return write_note(record, vault, today_iso=args.today)

    return run_write(write)


if __name__ == "__main__":
    raise SystemExit(main())
