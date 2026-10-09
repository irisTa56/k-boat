"""The vault's stored `notebooklm_id`s, read against a saved NotebookLM listing.

Pure joins over two inputs the CLI has already read: the source notes'
frontmatter and the `notebooks` array of `notebooklm list --json`. No NotebookLM
call and no judgement — which absences mean a notebook is gone and which mean the
listing is another account's is the caller's to decide from `counts`
(`kboat-notes`, the restore procedure's step 1).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from kboat.frontmatter import Value

# The id alone answers `resolve`; the sweep also reads what its set turns on, across
# the vault, and what an entry shows of its note, which matters only on a note shown.
ID_FIELDS = ("notebooklm_id",)
MEMBERSHIP_FIELDS = (*ID_FIELDS, "reading", "distill", "dismiss", "blocked", "distilled_date")
SHOWN_FIELDS = ("title", "url", "source_type")


class NotAListingError(ValueError):
    """The file is not what `notebooklm list --json` prints on success."""


@dataclass(frozen=True)
class Notebook:
    id: str
    title: str
    owned: bool


@dataclass(frozen=True)
class Source:
    slug: str
    frontmatter: dict[str, Value]
    shown: dict[str, Value]

    @property
    def notebook_id(self) -> str:
        value = self.frontmatter.get("notebooklm_id")
        return value if isinstance(value, str) else ""


def parse_listing(payload: object) -> list[Notebook]:
    """The notebooks a listing payload holds, or `NotAListingError`.

    Strict on purpose: a failed `notebooklm list … --json > file` leaves an empty
    file or the CLI's `--json` error object, and either read as an account with no
    notebooks would report every stored id as a notebook that is gone.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("notebooks"), list):
        raise NotAListingError("no `notebooks` array")
    notebooks: list[Notebook] = []
    for entry in payload["notebooks"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise NotAListingError("a `notebooks` entry with no `id`")
        title = entry.get("title")
        notebooks.append(
            Notebook(
                id=entry["id"],
                title=title if isinstance(title, str) else "",
                # Only a listing that says a notebook is someone else's leaves it out.
                owned=entry.get("is_owner") is not False,
            )
        )
    return notebooks


def in_sweep_set(fm: dict[str, Value]) -> bool:
    """`kboat-notebook-health`'s own set: a source the reader has opened that
    nothing else watches — not in the DLQ, not dismissed, not awaiting distillation."""
    if fm.get("reading") is not True:
        return False
    if fm.get("blocked") is True or fm.get("dismiss") is True:
        return False
    return not (fm.get("distill") is True and not fm.get("distilled_date"))


def _counts(stored: Sequence[Source], listed: set[str]) -> dict[str, int]:
    return {
        "stored_ids": len(stored),
        "resolved_ids": sum(1 for s in stored if s.notebook_id in listed),
    }


def sweep(sources: Sequence[Source], notebooks: Sequence[Notebook]) -> dict[str, object]:
    listed = {nb.id for nb in notebooks}
    stored = [s for s in sources if s.notebook_id]
    sweep_set: list[dict[str, object]] = []
    absent: list[dict[str, object]] = []
    for source in stored:
        fm = source.frontmatter
        if source.notebook_id not in listed:
            absent.append(
                {
                    "slug": source.slug,
                    "title": source.shown.get("title"),
                    "notebooklm_id": source.notebook_id,
                    "in_sweep_set": in_sweep_set(fm),
                }
            )
        elif in_sweep_set(fm):
            sweep_set.append(
                {
                    "slug": source.slug,
                    **{k: source.shown.get(k) for k in SHOWN_FIELDS},
                    "notebooklm_id": source.notebook_id,
                }
            )
    counts = _counts(stored, listed)
    referenced = {s.notebook_id for s in stored}
    return {
        "sweep_set": sweep_set,
        "absent": absent,
        # No list where no stored id resolves: under another account every notebook
        # listed is one no note names, and a vault holding no id gives the listing
        # nothing to be checked against.
        "unreferenced": [
            {"id": nb.id, "title": nb.title}
            for nb in notebooks
            if nb.owned and nb.id not in referenced
        ]
        if counts["resolved_ids"]
        else None,
        "counts": counts,
    }


def resolve(
    sources: Sequence[Source], notebooks: Sequence[Notebook], ids: Sequence[str]
) -> dict[str, object]:
    listed = {nb.id for nb in notebooks}
    stored = [s for s in sources if s.notebook_id]
    return {
        "ids": [{"notebooklm_id": i, "listed": i in listed} for i in ids],
        "counts": _counts(stored, listed),
    }
