"""Derive a repo's activity `status` from its last push.

Pure date math — the spec lives in `kboat-notes` ("Repo note"); this is its
implementation. `today` is injected so the buckets are testable and a run is
reproducible.

Buckets (mirroring the spec):
    archived  — GitHub's archived flag is set
    unknown   — no push timestamp available
    recent    — <= 60 days   (~2 months)
    active    — <= 180 days  (~6 months)
    slow      — <= 730 days  (~2 years)
    dormant   — > 730 days
"""

from __future__ import annotations

from datetime import date


def derive_status(archived: bool, pushed_at_iso: str | None, *, today: date) -> str:
    if archived:
        return "archived"
    if not pushed_at_iso:
        return "unknown"
    pushed = date.fromisoformat(pushed_at_iso[:10])
    days = (today - pushed).days
    if days > 730:
        return "dormant"
    if days > 180:
        return "slow"
    if days > 60:
        return "active"
    return "recent"
