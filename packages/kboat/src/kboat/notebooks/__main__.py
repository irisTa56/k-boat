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

from .core import (
    ID_FIELDS,
    MEMBERSHIP_FIELDS,
    SHOWN_FIELDS,
    NotAListingError,
    Notebook,
    Source,
    parse_listing,
    resolve,
    sweep,
)


def _read_listing(path: Path) -> list[Notebook]:
    try:
        return parse_listing(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError) as exc:
        raise NotAListingError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise NotAListingError(f"not JSON ({exc})") from exc


def _entries(report: dict[str, object], key: str) -> list[dict]:
    entries = report[key]
    assert isinstance(entries, list)
    return entries


def _cmd_resolve(
    vault: Path, notebooks: list[Notebook], ids: list[str]
) -> tuple[dict[str, object], bool]:
    report, unread = list_notes(vault, "source", fields=ID_FIELDS)
    sources = [Source(n["slug"], n["frontmatter"], {}) for n in _entries(report, "notes")]
    return {**resolve(sources, notebooks, ids), "anomalies": report["anomalies"]}, unread


def _cmd_sweep(vault: Path, notebooks: list[Notebook]) -> tuple[dict[str, object], bool]:
    """Two reads of `Sources/`, so each `anomalies` entry means what its reader takes
    it for: a note whose id or membership could not be read, anywhere in the vault,
    or a field the report shows, on a note it shows. A shown field no entry carries
    is no gap in the answer, and one read would report it for every note alike."""
    report, unread = list_notes(vault, "source", fields=MEMBERSHIP_FIELDS)
    shown_report, shown_unread = list_notes(vault, "source", fields=SHOWN_FIELDS)
    shown = {n["slug"]: n["frontmatter"] for n in _entries(shown_report, "notes")}
    notes = _entries(report, "notes")
    sources = [Source(n["slug"], n["frontmatter"], shown.get(n["slug"], {})) for n in notes]
    output = sweep(sources, notebooks)
    slugs = {e["slug"] for key in ("sweep_set", "absent") for e in _entries(output, key)}
    paths = {n["path"] for n in notes if n["slug"] in slugs}
    output["anomalies"] = [
        *_entries(report, "anomalies"),
        # A second read that met no folder holds that one entry, and every entry
        # the report shows is then without the fields this read was for. Where the
        # first met none either, its own entry already says so.
        *(
            a
            for a in _entries(shown_report, "anomalies")
            if (shown_unread and not unread) or a["path"] in paths
        ),
    ]
    return output, unread or shown_unread


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

    if args.command == "sweep":
        output, unread = _cmd_sweep(vault, notebooks)
    else:
        output, unread = _cmd_resolve(vault, notebooks, args.ids)
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if unread else 0


if __name__ == "__main__":
    raise SystemExit(main())
