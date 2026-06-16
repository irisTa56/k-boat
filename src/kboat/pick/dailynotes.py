"""Read the recent Daily-note bodies, newest-first, for the daily pick.

Daily notes live in `Daily/` named `YYYY-MM-DD.md` (the Obsidian core Daily Notes
plugin). The daily pick reads what the human has been writing lately and lets the
ranker (`kboat-recall`) infer their current interests from that content — there is
no prescribed heading or format. We walk the notes newest-first, up to `today` and
back no further than a look-back window (default two weeks), so a day with no note
is simply skipped and only the recent notes are used. Bounding the window keeps the
pick anchored to what the human is engaged with now rather than stale notes.
Non-date-named files in `Daily/` are not daily notes and are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from kboat.frontmatter import strip_frontmatter

DEFAULT_LOOKBACK_DAYS = 14


@dataclass(frozen=True)
class DailyNote:
    date: str  # YYYY-MM-DD
    body: str  # markdown body, frontmatter stripped, surrounding whitespace trimmed


def _parse_date(stem: str) -> date | None:
    try:
        return date.fromisoformat(stem)
    except ValueError:
        return None


def extract_daily_notes(
    daily_dir: Path, today: date, lookback_days: int = DEFAULT_LOOKBACK_DAYS
) -> list[DailyNote]:
    """Recent Daily-note bodies, newest-first, within the look-back window.

    A day is in scope when its date `d` satisfies `earliest <= d <= today`, where
    `earliest = today - lookback_days` (the window is inclusive of both ends). A note
    whose body (after stripping frontmatter) is empty or only whitespace is skipped,
    so it carries no signal into the pick.
    """
    days: list[DailyNote] = []
    if not daily_dir.is_dir():
        return days
    earliest = today - timedelta(days=lookback_days)
    for path in daily_dir.glob("*.md"):
        d = _parse_date(path.stem)
        if d is None or d > today or d < earliest:
            continue
        body = strip_frontmatter(path.read_text(encoding="utf-8")).strip()
        if body:
            days.append(DailyNote(date=d.isoformat(), body=body))
    days.sort(key=lambda dn: dn.date, reverse=True)
    return days
