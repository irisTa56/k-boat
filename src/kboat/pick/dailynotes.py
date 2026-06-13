"""Extract the `## 明日への問い` questions from the Daily notes, newest-first.

Daily notes live in `Daily/` named `YYYY-MM-DD.md` (the Obsidian core Daily Notes
plugin). The daily pick reads the questions a human wrote under a
`## 明日への問い` heading. We walk the notes newest-first, up to `today` and back
no further than a look-back window (default two weeks), so a day with no note — or
a note without the heading — is simply skipped and only the recent questions are
used. Bounding the window keeps the pick anchored to what the human is asking now
rather than ranking against stale questions. Non-date-named files in `Daily/` are
not daily notes and are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

QUESTION_HEADING = "## 明日への問い"
DEFAULT_LOOKBACK_DAYS = 14


@dataclass(frozen=True)
class DayQuestions:
    date: str  # YYYY-MM-DD
    items: list[str]


def _parse_date(stem: str) -> date | None:
    try:
        return date.fromisoformat(stem)
    except ValueError:
        return None


def _section_items(text: str, heading: str) -> list[str]:
    """Return the `- item` bullets directly under `heading`, until the next
    heading or end of file. Empty bullets (a seeded `-` placeholder) are dropped."""
    items: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            in_section = stripped == heading
            continue
        if in_section:
            if stripped.startswith(("- ", "* ")):
                item = stripped[2:].strip()
                if item:
                    items.append(item)
            elif stripped in ("-", "*"):
                continue  # empty placeholder bullet
    return items


def extract_questions(
    daily_dir: Path, today: date, lookback_days: int = DEFAULT_LOOKBACK_DAYS
) -> list[DayQuestions]:
    """Daily-note questions, newest-first, within the look-back window.

    A day is in scope when its date `d` satisfies `earliest <= d <= today`, where
    `earliest = today - lookback_days` (the window is inclusive of both ends). Only
    days whose `## 明日への問い` section has at least one non-empty bullet appear in
    the result.
    """
    days: list[DayQuestions] = []
    if not daily_dir.is_dir():
        return days
    earliest = today - timedelta(days=lookback_days)
    for path in daily_dir.glob("*.md"):
        d = _parse_date(path.stem)
        if d is None or d > today or d < earliest:
            continue
        items = _section_items(path.read_text(encoding="utf-8"), QUESTION_HEADING)
        if items:
            days.append(DayQuestions(date=d.isoformat(), items=items))
    days.sort(key=lambda dq: dq.date, reverse=True)
    return days
