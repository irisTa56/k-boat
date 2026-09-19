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

from kboat.cli import EVICTED_NOTE
from kboat.frontmatter import PLAIN_READ_ERRORS, strip_frontmatter
from kboat.io_utils import list_note_dir, unread_dir

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


def _in_window(stem: str, today: date, earliest: date) -> date | None:
    d = _parse_date(stem)
    return d if d is not None and earliest <= d <= today else None


def extract_daily_notes(
    daily_dir: Path, today: date, lookback_days: int = DEFAULT_LOOKBACK_DAYS
) -> tuple[list[DailyNote], list[dict[str, str]]]:
    """Recent Daily-note bodies newest-first, and what in scope could not be read.

    A day is in scope when its date `d` satisfies `earliest <= d <= today`, where
    `earliest = today - lookback_days` (the window is inclusive of both ends). A note
    whose body (after stripping frontmatter) is empty or only whitespace is skipped,
    so it carries no signal into the pick.

    Each `path` in the second list is qualified with the folder's own name, since
    one entry can stand for the folder itself.
    """
    days: list[DailyNote] = []
    unreadable: list[dict[str, str]] = []
    # Not required: `Daily/` is optional by design, so an absent one yields nothing
    # and says nothing. A refused one is reported rather than read as a fortnight
    # the reader wrote nothing in — nothing else would tell the two apart, since
    # `kboat-doctor` deliberately does not check this folder.
    try:
        found, placeholders = list_note_dir(daily_dir)
    except OSError as exc:
        return days, [{"path": daily_dir.name, "error": unread_dir(exc)}]
    earliest = today - timedelta(days=lookback_days)
    for placeholder in placeholders:
        # The same date-name test and window the readable notes take below. This is
        # the only report an evicted daily note ever gets, and with Optimize
        # Storage the oldest untouched notes are the first iCloud evicts — so
        # reporting one the pick was never going to read would make a permanent
        # daily anomaly out of the back of the folder.
        stem = placeholder.name.removeprefix(".").removesuffix(".icloud").removesuffix(".md")
        if _in_window(stem, today, earliest) is None:
            continue
        unreadable.append({"path": f"{daily_dir.name}/{placeholder.name}", "error": EVICTED_NOTE})
    for path in found:
        d = _in_window(path.stem, today, earliest)
        if d is None:
            continue
        # Reported rather than skipped: nothing else would tell a note that could not be read
        # from a day with no note.
        try:
            body = strip_frontmatter(path.read_text(encoding="utf-8")).strip()
        except PLAIN_READ_ERRORS as exc:
            unreadable.append({"path": f"{daily_dir.name}/{path.name}", "error": str(exc)})
            continue
        if body:
            days.append(DailyNote(date=d.isoformat(), body=body))
    days.sort(key=lambda dn: dn.date, reverse=True)
    return days, unreadable
