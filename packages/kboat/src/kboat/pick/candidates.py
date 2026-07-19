"""Select the active web inbox — the candidate pool for a daily pick.

A candidate is a not-yet-started, undispositioned web-page source: the Reading
Inbox "Web" view minus its in-progress (`reading`) reads, a strict subset. The
daily pick only ever surfaces web pages, and only ones you have not started:
anything you are already mid-read appears in the Today view via `reading`
whatever its type, so PDFs and in-progress web pages are both excluded here. Each
candidate carries the durable `summary`/`topics` the ranker reads for the local
pre-filter, plus `added_date` for the diversification preference (one older + one
newer) and `notebooklm_id` so the ranker can fetch the shortlist's NotebookLM
fulltext for the body-read final judgment (see kboat-notes "Daily pick"). The id
may be empty (a candidate whose notebook is gone), which the ranker treats as
un-fetchable.
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
    notebooklm_id: str

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
            "notebooklm_id": self.notebooklm_id,
        }


def is_active_web(fm: dict[str, Value]) -> bool:
    """A not-yet-started, undispositioned web-page source (`!reading && !distill
    && !keep && !dismiss && !blocked && source_type == "web_page"`). The `reading`
    exclusion is what makes this a strict subset of the Sources "Web" view
    (which keeps in-progress reads): a source you are mid-read already shows in the
    Today view via `reading`, so a pick on it would surface nothing new — see
    kboat-notes "Daily pick"."""
    if fm.get("type") != "source" or fm.get("source_type") != "web_page":
        return False
    return not any(
        _as_bool(fm.get(k)) for k in ("reading", "distill", "keep", "dismiss", "blocked")
    )


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
        notebooklm_id=_as_str(fm.get("notebooklm_id")),
    )
