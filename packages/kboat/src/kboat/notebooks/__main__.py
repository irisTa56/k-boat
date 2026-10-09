"""CLI entry point: `kboat-notebooks`.

Two read-only subcommands over one join — the vault's stored `notebooklm_id`s
against a NotebookLM listing the caller saved with
`notebooklm --quiet list --json 2>/dev/null > <file>`. The tool calls NotebookLM
itself never; both take the saved file as `--notebooks`.

- `sweep` — `kboat-notebook-health`'s routine opening: the set to check, every
  stored id the listing lacks, the owned notebooks no note references, and the
  counts that tell a notebook gone from a listing fetched under another account.
- `resolve --id <id>…` — whether each given id is in the listing, beside the same
  counts, for a step that has one source's id in hand (`kboat-notes`, the restore
  procedure's step 1).

Both read `Sources/` through `kboat-note list`'s reader, so what the answer leaves
out is in `anomalies`, and a `Sources/` that could not be read is one entry under
its own name and exit 1 with the report still printed (`kboat-vault-conventions`
"Vault preconditions"). A `--notebooks` file that is not a listing is exit 2 and an
empty stdout: nothing can be read against it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kboat.cli import add_vault_argument, vault_path
from kboat.note.listing import list_notes

from .core import READ_FIELDS, NotAListingError, Notebook, Source, parse_listing, resolve, sweep


def _read_listing(path: Path) -> list[Notebook]:
    try:
        return parse_listing(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError) as exc:
        raise NotAListingError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise NotAListingError(f"not JSON ({exc})") from exc


def _read_sources(vault: Path) -> tuple[list[Source], object, bool]:
    report, unread = list_notes(vault, "source", fields=READ_FIELDS)
    notes = report["notes"]
    assert isinstance(notes, list)
    return [Source(n["slug"], n["frontmatter"]) for n in notes], report["anomalies"], unread


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-notebooks",
        description="Read the vault's stored notebook ids against a saved `notebooklm list --json`.",
    )
    add_vault_argument(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    p_sweep = sub.add_parser(
        "sweep",
        help="Print the notebook-health sweep set, the stored ids the listing lacks, and the notebooks no note references.",
    )
    p_resolve = sub.add_parser(
        "resolve", help="Print whether each given notebook id is in the listing."
    )
    p_resolve.add_argument(
        "--id",
        dest="ids",
        action="append",
        required=True,
        metavar="NOTEBOOKLM_ID",
        help="A notebook id to look for (repeatable).",
    )
    for p in (p_sweep, p_resolve):
        p.add_argument(
            "--notebooks",
            required=True,
            type=Path,
            metavar="FILE",
            help="The saved output of `notebooklm --quiet list --json`.",
        )

    args = parser.parse_args(argv)
    vault = vault_path(parser, args)
    try:
        notebooks = _read_listing(args.notebooks)
    except NotAListingError as exc:
        sys.stderr.write(f"{args.notebooks}: not a notebook listing: {exc}\n")
        return 2

    sources, anomalies, unread = _read_sources(vault)
    output = (
        sweep(sources, notebooks)
        if args.command == "sweep"
        else resolve(sources, notebooks, args.ids)
    )
    output["anomalies"] = anomalies
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if unread else 0


if __name__ == "__main__":
    raise SystemExit(main())
