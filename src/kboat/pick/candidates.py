"""Select the active web inbox — the candidate pool for a daily pick.

A candidate is a readable, undispositioned web-page source: the same set the
Reading Inbox "Web" view shows. The daily pick only ever surfaces web pages
(anything you are already mid-read appears in the Today view via `reading`,
whatever its type), so PDFs are excluded here. Each candidate carries the
durable `summary`/`topics` the ranker reads to judge relevance against the
interests inferred from the recent Daily notes, plus `added_date` for the daily
pick's diversification preference (one older + one newer — see kboat-notes
"Daily pick").
"""

from __future__ import annotations

from dataclasses import dataclass

from .notes import Value


def _as_bool(value: Value) -> bool:
    return value is True


def _as_str(value: Value) -> str:
    return value if isinstance(value, str) else ""


def _as_list(value: Value) -> list[str]:
    return value if isinstance(value, list) else []


@dataclass(frozen=True)
class Candidate:
    slug: str
    path: str
    title: str
    url: str
    reading_link: str
    summary: str
    topics: list[str]
    added_date: str

    def to_json(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "path": self.path,
            "title": self.title,
            "url": self.url,
            "reading_link": self.reading_link,
            "summary": self.summary,
            "topics": self.topics,
            "added_date": self.added_date,
        }


def is_active_web(fm: dict[str, Value]) -> bool:
    """A readable, undispositioned web-page source (`!distill && !keep &&
    !dismiss && !blocked && source_type == "web_page"`)."""
    if fm.get("type") != "source" or fm.get("source_type") != "web_page":
        return False
    return not any(_as_bool(fm.get(k)) for k in ("distill", "keep", "dismiss", "blocked"))


def candidate_from(slug: str, rel_path: str, fm: dict[str, Value]) -> Candidate:
    return Candidate(
        slug=slug,
        path=rel_path,
        title=_as_str(fm.get("title")),
        url=_as_str(fm.get("url")),
        reading_link=_as_str(fm.get("reading_link")),
        summary=_as_str(fm.get("summary")),
        topics=_as_list(fm.get("topics")),
        added_date=_as_str(fm.get("added_date")),
    )
