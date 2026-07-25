"""CLI entry point: `kboat-queue`.

- `list` — parse every `Queue/*.md` capture in the vault into `{path, url, title}`
  JSON for `kboat-ingest` to drain. Read-only: it never deletes a capture (ingest
  removes each file itself at its commit point). A malformed capture (no parseable
  `http(s)` link) comes back with `url: null` and `error: "no_url"` so the caller
  can report it rather than guess a URL.

Defaults the vault to `$OBSIDIAN_VAULT_PATH` and the folder to `Queue`, mirroring the
other kboat tools.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kboat.cli import add_vault_argument, vault_path

from .parse import parse_capture


def _cmd_list(vault: Path, folder: str) -> dict[str, object]:
    queue_dir = vault / folder
    files: list[dict[str, object]] = []
    for path in sorted(queue_dir.glob("*.md")) if queue_dir.is_dir() else []:
        rel = path.relative_to(vault).as_posix()
        try:
            capture = parse_capture(path.read_text(encoding="utf-8"))
        except OSError as exc:
            files.append({"path": rel, "url": None, "title": "", "error": str(exc)})
            continue
        entry: dict[str, object] = {"path": rel, "url": capture.url, "title": capture.title}
        if capture.url is None:
            entry["error"] = "no_url"
        files.append(entry)
    malformed = sum(1 for f in files if "error" in f)
    return {
        "vault": str(vault),
        "folder": folder,
        "files": files,
        "counts": {"total": len(files), "malformed": malformed},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-queue",
        description="Parse the vault's Queue/ capture notes for ingest (read-only).",
    )
    add_vault_argument(parser)
    parser.add_argument(
        "--folder",
        default="Queue",
        help="Vault-relative queue folder to read (default: Queue).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="Print each Queue/*.md capture as {path, url, title} JSON.")

    args = parser.parse_args(argv)

    vault = vault_path(parser, args)

    output = _cmd_list(vault, args.folder)
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
