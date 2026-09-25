"""CLI entry point: `kboat-queue`.

- `list` — parse every `Queue/*.md` capture in the vault into `{path, url, title}`
  JSON for `kboat-ingest` to drain. Read-only: it takes no lock and deletes
  nothing. A malformed capture (no parseable
  `http(s)` link) comes back with `url: null` and `error: "no_url"` so the caller
  can report it rather than guess a URL. What never became a capture is in
  `anomalies`: a queue folder that is absent, not a directory, or refused — which
  also exits 1, the folder being in the vault's required set — and each capture
  iCloud evicted to a placeholder.
- `remove <path>` — delete one capture `list` printed, once ingest has reached
  its commit point, under the vault lock. It prints `{path, status, stranded}`:
  `status` is `removed`, or `absent` when the capture was already gone, and
  `stranded` names the iCloud stub the removal left beside it (null for none).
  It refuses, with a usage error and nothing deleted, any path that is not a
  `.md` file directly in the queue folder.

Defaults the vault to `$OBSIDIAN_VAULT_PATH` and the folder to `Queue`, mirroring the
other kboat tools.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath

from kboat.cli import (
    add_vault_argument,
    emit_lock_unavailable,
    emit_locked,
    scan_required_dir,
    vault_path,
)
from kboat.frontmatter import PLAIN_READ_ERRORS
from kboat.io_utils import stranded_stub
from kboat.lock import VaultLockedError, VaultLockUnavailableError, vault_lock
from kboat.schema import QUEUE_DIR

from .parse import parse_capture


def _cmd_list(vault: Path, folder: str) -> tuple[dict[str, object], bool]:
    """The queue report, and whether the queue folder itself could not be read.

    Neither an unlistable folder nor an evicted capture may read as a drained
    queue, which is what tells ingest there is nothing to do: the first is an
    `anomalies` entry under the folder's name and exit 1, the second one under the
    placeholder's path.
    """
    files: list[dict[str, object]] = []
    found, anomalies, unread = scan_required_dir(vault, folder)
    for path in found:
        rel = path.relative_to(vault).as_posix()
        try:
            capture = parse_capture(path.read_text(encoding="utf-8"))
        except PLAIN_READ_ERRORS as exc:
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
        "anomalies": anomalies,
    }, unread


def _cmd_remove(vault: Path, rel: str) -> dict[str, object]:
    """Delete one capture and report the stub the deletion strands, if any.

    The probe runs before the unlink because a stub beside a present capture is
    not yet an eviction; the unlink is what makes it one (`stranded_stub`). A
    capture that was already gone vacated nothing, so it strands nothing either:
    a stub there is the capture itself, evicted, and `list` reports it.
    """
    capture = vault / rel
    with vault_lock(vault):
        probe = stranded_stub(capture)
        try:
            capture.unlink()
        except FileNotFoundError:
            status = "absent"
        else:
            status = "removed"
    stranded: str | None = None
    if status == "removed" and probe.stub is not None:
        stranded = probe.stub.relative_to(vault).as_posix()
    elif status == "removed" and probe.unknown is not None:
        stranded = f"unknown: {probe.unknown}"
    return {"path": rel, "status": status, "stranded": stranded}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-queue",
        description="Read the vault's Queue/ capture notes for ingest, and delete a drained one.",
    )
    add_vault_argument(parser)
    parser.add_argument(
        "--folder",
        default=QUEUE_DIR,
        help=f"Vault-relative queue folder to read (default: {QUEUE_DIR}).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="Print each Queue/*.md capture as {path, url, title} JSON.")

    remove = sub.add_parser("remove", help="Delete one drained capture.")
    remove.add_argument("path", help="The capture's vault-relative path, as `list` prints it.")

    args = parser.parse_args(argv)

    vault = vault_path(parser, args)

    if args.command == "remove":
        rel = PurePosixPath(args.path)
        # The allow rule that lets an unattended run call this names the command,
        # not the path, so the command itself keeps the deletion to one capture.
        if rel.parts[:-1] != PurePosixPath(args.folder).parts or rel.suffix != ".md":
            parser.error(f"not a capture directly under {args.folder}/: {args.path}")
        try:
            report = _cmd_remove(vault, args.path)
        except VaultLockedError as exc:
            return emit_locked(exc)
        except VaultLockUnavailableError as exc:
            return emit_lock_unavailable(exc)
        except OSError as exc:
            sys.stderr.write(f"remove failed: {exc}\n")
            return 1
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    output, unread = _cmd_list(vault, args.folder)
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if unread else 0


if __name__ == "__main__":
    raise SystemExit(main())
