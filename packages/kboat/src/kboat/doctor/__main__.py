"""CLI entry point: `kboat-doctor`.

Checks the vault's environment preconditions (`kboat-vault-conventions`, "Vault
preconditions") and prints every check as JSON on stdout. Exit 0 when nothing
failed, 1 when anything did — with each failed check also written to stderr, so
an unattended run's log shows the reason without parsing the JSON back.

A run calls this before its first phase: the checks are what the phases assume
and cannot verify for themselves, and a vault that is absent, unwritable,
unreadable, or half-synced makes every later report a report about a vault that
was not there.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from kboat.cli import add_vault_argument, vault_path

from .core import Check, Status, run_checks


def _where(check: Check) -> str:
    """The paths on the diagnostic line, under `MAX_REPORT_PATHS` like the JSON."""
    if not check.paths:
        return ""
    shown = ", ".join(check.reported_paths)
    rest = len(check.paths) - len(check.reported_paths)
    return f" — {shown}" + (f" … and {rest} more" if rest > 0 else "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-doctor",
        description="Check the vault's environment preconditions before a run.",
    )
    add_vault_argument(parser)
    args = parser.parse_args(argv)

    vault = vault_path(parser, args)

    checks = run_checks(vault)
    by_status = Counter(c.status for c in checks)
    failures = [c for c in checks if c.status == Status.FAILED]
    output = {
        "vault": str(vault),
        "ok": not failures,
        "checks": [c.to_json() for c in checks],
        # Every status key is present even at zero: a reader keying on
        # `counts["failed"]` should not have to know that an absent key means
        # none. A short-circuited run reports fewer checks, not fewer keys.
        "counts": {"total": len(checks), **{s: by_status[s] for s in Status}},
    }
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    # Warnings reach stderr too, since an unattended log is the only place a
    # non-failing finding would otherwise be seen. The paths go on the line as
    # well, so reading the JSON back is not needed to know what to act on.
    for check in checks:
        if check.status != Status.OK:
            sys.stderr.write(f"{check.status}: {check.name}: {check.detail}{_where(check)}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
