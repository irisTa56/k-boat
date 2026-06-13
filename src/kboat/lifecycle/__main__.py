"""CLI entry point: `kboat-lifecycle`.

Reads every `Sources/*.md` note in the vault, maintains the cooldown clock
(Phase A — stamps/clears `filed_date` on disk unless `--dry-run`), and prints
the resulting plan as JSON on stdout for the `kboat-distill` routine to act on.

Alongside the cooldown work sets it emits `needs_summary`: sources with a live
notebook but an empty `summary`/`topics`, the recovery set the ingest pass
retries (re-fetch the source guide while the notebook still exists). It is a
read-only listing — no writes are tied to it — so `kboat-ingest` reads it from a
`--dry-run` invocation without triggering any `filed_date` change.

It also scans `Kindles/*.md` (if present) and emits the ripe Kindle set under
`kindles.ripe`. Kindle notes have no cooldown and no notebook, so the tool makes
no on-disk writes for them — it only selects which are ripe (`distill` &&
`distilled_date` empty).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from .core import Kindle, Source, compute_plan, select_ripe_kindles
from .notes import FrontmatterError, parse_frontmatter, set_filed_date


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


def _load_sources(sources_dir: Path, vault: Path) -> tuple[list[Source], list[dict[str, str]]]:
    sources: list[Source] = []
    anomalies: list[dict[str, str]] = []
    for path in sorted(sources_dir.glob("*.md")):
        rel = path.relative_to(vault).as_posix()
        try:
            fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        except (FrontmatterError, OSError) as exc:
            anomalies.append({"path": rel, "error": str(exc)})
            continue
        if fm.get("type") != "source":
            anomalies.append({"path": rel, "error": "frontmatter 'type' is not 'source'"})
            continue
        sources.append(Source.from_frontmatter(path.stem, rel, fm))
    return sources, anomalies


def _load_kindles(kindles_dir: Path, vault: Path) -> tuple[list[Kindle], list[dict[str, str]]]:
    """Scan `Kindles/*.md`. The directory is optional — an absent one yields no
    Kindle notes and no anomalies (a vault may have only sources)."""
    kindles: list[Kindle] = []
    anomalies: list[dict[str, str]] = []
    if not kindles_dir.is_dir():
        return kindles, anomalies
    for path in sorted(kindles_dir.glob("*.md")):
        rel = path.relative_to(vault).as_posix()
        try:
            fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        except (FrontmatterError, OSError) as exc:
            anomalies.append({"path": rel, "error": str(exc)})
            continue
        if fm.get("type") != "kindle":
            anomalies.append({"path": rel, "error": "frontmatter 'type' is not 'kindle'"})
            continue
        kindles.append(Kindle.from_frontmatter(path.stem, rel, fm))
    return kindles, anomalies


def _apply_phase_a(
    plan_stamp: list[Source],
    plan_clear: list[Source],
    today_iso: str,
    vault: Path,
) -> list[dict[str, str]]:
    """Rewrite the `filed_date` line on disk. Returns per-note write anomalies."""
    anomalies: list[dict[str, str]] = []
    for s, value in [(s, today_iso) for s in plan_stamp] + [(s, None) for s in plan_clear]:
        path = vault / s.path
        try:
            path.write_text(
                set_filed_date(path.read_text(encoding="utf-8"), value), encoding="utf-8"
            )
        except (FrontmatterError, OSError) as exc:
            anomalies.append({"path": s.path, "error": f"filed_date write failed: {exc}"})
    return anomalies


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kboat-lifecycle",
        description="Maintain the K-Boat cooldown clock and compute the distill work sets.",
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the plan without writing any filed_date changes.",
    )
    args = parser.parse_args(argv)

    if not args.vault:
        parser.error("no vault: pass --vault or set OBSIDIAN_VAULT_PATH")
    try:
        today = date.fromisoformat(args.today)
    except ValueError:
        parser.error(f"--today must be YYYY-MM-DD, got {args.today!r}")

    vault = Path(args.vault).expanduser()
    sources_dir = vault / "Sources"
    if not sources_dir.is_dir():
        parser.error(f"no Sources/ directory under vault: {sources_dir}")

    sources, anomalies = _load_sources(sources_dir, vault)
    plan = compute_plan(sources, today)

    # Kindle notes (Kindles/ is optional). No on-disk writes — Kindle has no
    # cooldown clock — only ripe selection.
    kindles, kindle_anomalies = _load_kindles(vault / "Kindles", vault)
    anomalies += kindle_anomalies
    ripe_kindles = select_ripe_kindles(kindles)

    if not args.dry_run:
        anomalies += _apply_phase_a(
            plan.phase_a_stamp, plan.phase_a_clear, today.isoformat(), vault
        )

    counts = dict(plan.counts)
    counts["kindles_total"] = len(kindles)
    counts["kindles_ripe"] = len(ripe_kindles)
    counts["kindles_already_distilled"] = sum(1 for k in kindles if k.distilled_date is not None)

    output = {
        "today": plan.today,
        "vault": str(vault),
        "dry_run": args.dry_run,
        "phase_a": {
            "stamped": [_source_json(s) for s in plan.phase_a_stamp],
            "cleared": [_source_json(s) for s in plan.phase_a_clear],
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
    }
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
