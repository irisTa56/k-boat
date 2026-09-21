"""CLI entry point: `kboat-lifecycle`.

Reads every `Sources/*.md` note in the vault, maintains the cooldown clock
(Phase A — stamps/clears `filed_date` on disk unless `--dry-run`), and prints
the resulting plan as JSON on stdout for the `kboat-distill` routine to act on.

An applying run holds the vault lock across its read and its writes, and reports
a `locked` record rather than racing a run that already has it; a `--dry-run`
writes nothing, so it takes no lock and reads a vault another run is writing.

Alongside the cooldown work sets it emits `needs_summary`: sources with a live
notebook but an empty `summary`/`topics`, the recovery set the ingest pass
retries (re-fetch the source guide while the notebook still exists). It is a
read-only listing — no writes are tied to it — so `kboat-ingest` reads it from a
`--dry-run` invocation without triggering any `filed_date` change.

It also scans `Kindles/*.md` and emits the ripe Kindle set under
`kindles.ripe`. Kindle notes have no cooldown and no notebook, so the tool makes
no on-disk writes for them — it only selects which are ripe (`distill` &&
`distilled_date` empty).

`Sources/` and `Kindles/` are both in the vault's required set, so either one
absent, not a directory, or refused is an `anomalies` entry under the folder's own
name and exit 1, with the rest of the report still printed and acted on
(`kboat-vault-conventions` "Vault preconditions").
"""

from __future__ import annotations

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
from kboat.schema import DIR_BY_TYPE

from .core import Kindle, Source, compute_plan, select_ripe_kindles
from .notes import NOTE_READ_ERRORS, parse_frontmatter, repeated_keys, set_filed_date


def _source_json(s: Source) -> dict[str, object]:
    return {
        "slug": s.slug,
        "path": s.path,
        "title": s.title,
        "source_type": s.source_type,
        "url": s.url,
        "distill": s.distill,
        "keep": s.keep,
        "dismiss": s.dismiss,
        "filed_date": s.filed_date,
        "distilled_date": s.distilled_date,
        "notebooklm_id": s.notebooklm_id,
    }


def _kindle_json(k: Kindle) -> dict[str, object]:
    return {
        "slug": k.slug,  # the bare ASIN — kboat-distill writes it as the ASIN:<asin> provenance value
        "path": k.path,
        "title": k.title,
        "distilled_date": k.distilled_date,
    }


def _load_sources(vault: Path) -> tuple[list[Source], list[dict[str, str]], bool]:
    """Scan `Sources/*.md`; the flag is whether `Sources/` itself could not be read."""
    sources: list[Source] = []
    found, anomalies, unread = scan_required_dir(vault, DIR_BY_TYPE["source"])
    for path in found:
        rel = path.relative_to(vault).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
        except NOTE_READ_ERRORS as exc:
            anomalies.append({"path": rel, "error": str(exc)})
            continue
        if fm.get("type") != "source":
            anomalies.append({"path": rel, "error": "frontmatter 'type' is not 'source'"})
            continue
        repeated = tuple(sorted(repeated_keys(text)))
        sources.append(Source.from_frontmatter(path.stem, rel, fm, repeated))
    return sources, anomalies, unread


def _load_kindles(vault: Path) -> tuple[list[Kindle], list[dict[str, str]], bool]:
    """Scan `Kindles/*.md`; the flag is whether `Kindles/` itself could not be read.

    A vault with no Kindle books still has the folder, empty: it is in the vault's
    required set, so an absent one is a vault that has not synced rather than one
    holding only sources, and reading it as no Kindle notes is how that vault
    would be distilled as though it were whole.
    """
    kindles: list[Kindle] = []
    found, anomalies, unread = scan_required_dir(vault, DIR_BY_TYPE["kindle"])
    for path in found:
        rel = path.relative_to(vault).as_posix()
        try:
            fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        except NOTE_READ_ERRORS as exc:
            anomalies.append({"path": rel, "error": str(exc)})
            continue
        if fm.get("type") != "kindle":
            anomalies.append({"path": rel, "error": "frontmatter 'type' is not 'kindle'"})
            continue
        kindles.append(Kindle.from_frontmatter(path.stem, rel, fm))
    return kindles, anomalies, unread


def _apply_phase_a(
    plan_stamp: list[Source],
    plan_clear: list[Source],
    today_iso: str,
    vault: Path,
) -> tuple[list[Source], list[Source], list[dict[str, str]]]:
    """Rewrite the `filed_date` line on disk.

    Returns the sources actually stamped, those actually cleared, and a write
    anomaly for each of the rest, so the report names a note whose write failed
    as that anomaly and not also as stamped or cleared.
    """
    written: dict[str | None, list[Source]] = {today_iso: [], None: []}
    anomalies: list[dict[str, str]] = []
    for s, value in [(s, today_iso) for s in plan_stamp] + [(s, None) for s in plan_clear]:
        path = vault / s.path
        try:
            atomic_write_text(path, set_filed_date(path.read_text(encoding="utf-8"), value))
        except NOTE_READ_ERRORS as exc:
            anomalies.append({"path": s.path, "error": f"filed_date write failed: {exc}"})
        else:
            written[value].append(s)
    return written[today_iso], written[None], anomalies


def _run(vault: Path, today: date, *, dry_run: bool) -> tuple[dict[str, object], bool]:
    """Read the vault, compute the plan, apply Phase A unless `dry_run`, and
    return the JSON output with whether a required folder could not be read.

    The read and the write are one step so that they happen under one hold of
    the vault lock: a plan computed before another run's writes would stamp
    dates the notes no longer call for.

    One unread folder does not stop the other: each work set is decided from its
    own notes alone, so what was read is reported and acted on, and the folder
    that was not is its own anomaly and the exit code.
    """
    sources, anomalies, sources_unread = _load_sources(vault)
    plan = compute_plan(sources, today)
    anomalies += [
        {
            "path": s.path,
            "error": f"notebook not discarded: the note names {', '.join(map(repr, s.repeated_keys))}"
            " on more than one line, so the note writer would refuse to clear its coordinates",
        }
        for s in plan.dismiss_held
    ]

    # No on-disk writes for Kindle notes — Kindle has no cooldown clock — only
    # ripe selection.
    kindles, kindle_anomalies, kindles_unread = _load_kindles(vault)
    anomalies += kindle_anomalies
    ripe_kindles = select_ripe_kindles(kindles)

    stamped, cleared = plan.phase_a_stamp, plan.phase_a_clear
    if not dry_run:
        stamped, cleared, write_anomalies = _apply_phase_a(
            stamped, cleared, today.isoformat(), vault
        )
        anomalies += write_anomalies

    counts = dict(plan.counts)
    counts["filed_stamped"] = len(stamped)
    counts["filed_cleared"] = len(cleared)
    counts["kindles_total"] = len(kindles)
    counts["kindles_ripe"] = len(ripe_kindles)
    counts["kindles_already_distilled"] = sum(1 for k in kindles if k.distilled_date is not None)

    return {
        "today": plan.today,
        "vault": str(vault),
        "dry_run": dry_run,
        "phase_a": {
            "stamped": [_source_json(s) for s in stamped],
            "cleared": [_source_json(s) for s in cleared],
        },
        "ambiguous": [_source_json(s) for s in plan.ambiguous],
        "phase_b": {
            "ripe": [_source_json(s) for s in plan.ripe],
            "dismiss_discard": [_source_json(s) for s in plan.dismiss_discard],
        },
        "needs_summary": [_source_json(s) for s in plan.needs_summary],
        "kindles": {
            "ripe": [_kindle_json(k) for k in ripe_kindles],
        },
        "counts": counts,
        "anomalies": anomalies,
    }, sources_unread or kindles_unread


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-lifecycle",
        description="Maintain the K-Boat cooldown clock and compute the distill work sets.",
    )
    add_vault_argument(parser)
    add_today_argument(parser)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the plan without writing any filed_date changes.",
    )
    args = parser.parse_args(argv)

    vault = vault_path(parser, args)
    today = date.fromisoformat(args.today)

    # No gate on `Sources/` ahead of the scan: an `is_dir()` there answered "no
    # such directory" for one that is there and unreadable — its `stat` goes
    # through the parent — and ended the run with no JSON before the scan that
    # would have said which it was. The scan reports all three states itself.
    #
    # A `--dry-run` writes nothing, so it takes no lock and reads a vault
    # another run is writing; an applying run holds the lock over read and write
    # alike, and reports rather than waits when another run already has it.
    if args.dry_run:
        output, unread = _run(vault, today, dry_run=True)
    else:
        try:
            with vault_lock(vault):
                output, unread = _run(vault, today, dry_run=False)
        except VaultLockedError as exc:
            return emit_locked(exc)
        except VaultLockUnavailableError as exc:
            return emit_lock_unavailable(exc)

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if unread else 0


if __name__ == "__main__":
    raise SystemExit(main())
