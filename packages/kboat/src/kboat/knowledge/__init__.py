"""Audit the knowledge base's concept notes for what a human curating it has to look at.

Two reads over `<knowledge root>/concepts/*.md`, both for `kboat-curate`:

- `flagged_titles` -- the notes whose title a wikilink cannot reach in a viewer that
  resolves `[[...]]` by filename. The rule is the `kboat-notes` skill's, "Concept
  notes": a title is its own filename, and carries none of the characters a wikilink
  reads as syntax.
- `tag_census` -- how often each frontmatter facet tag is used, and which notes carry
  none.

The notes are Basic Memory's rather than this package's, so their frontmatter is read
with a YAML loader and not with `kboat.frontmatter`. Basic Memory writes it with PyYAML
at the default width, which folds a long title onto a continuation line that the
scanner does not model. `BaseLoader` reads every scalar as the string written, so a
title such as `yes` or `1.10` compares against its filename as text.

The directory is listed with `kboat.io_utils.list_note_dir`, which raises where the OS
refuses the listing. A concept note that an iCloud placeholder stands in for is
refused rather than left out: a census that skipped it would describe a base it never
read in full. So is a note whose frontmatter does not parse as a YAML mapping. Read as
empty, it would pass for a note with no title and no tags, and the curate pass would
add a second `tags:` block to a note that already has one while its real break went
unfixed.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import yaml

from kboat.io_utils import list_note_dir

CONCEPTS_DIR = "concepts"

#: Characters Basic Memory keeps in a filename but a wikilink reads as syntax: `#`
#: opens a heading link, `#^` a block link, and a bracket ends the link.
WIKILINK_SYNTAX = "#^[]"

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*\r?$", re.DOTALL | re.MULTILINE)


class EvictedNotesError(Exception):
    """Concept notes that are iCloud placeholders, so their text cannot be read."""

    def __init__(self, placeholders: list[Path]) -> None:
        self.placeholders = placeholders
        super().__init__(f"{len(placeholders)} concept note(s) are iCloud placeholders")


class UnreadableNotesError(Exception):
    """Concept notes whose frontmatter does not parse as a YAML mapping."""

    def __init__(self, files: list[str]) -> None:
        self.files = files
        super().__init__(f"{len(files)} concept note(s) have frontmatter that does not parse")


@dataclass(frozen=True)
class ConceptNote:
    """One concept note as the audits read it."""

    file: str  # knowledge-root-relative POSIX path
    stem: str
    frontmatter: dict[str, object]


def frontmatter(text: str) -> dict[str, object] | None:
    """The note's frontmatter as strings, lists and mappings.

    `None` where the note has no frontmatter block, or the block is not YAML or not a
    mapping.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return None
    try:
        loaded = yaml.load(match.group(1), Loader=yaml.BaseLoader)
    except yaml.YAMLError:
        return None
    return loaded if isinstance(loaded, dict) else None


def read_concepts(root: Path) -> list[ConceptNote]:
    """Every concept note under `root`, sorted by filename.

    Raises `EvictedNotesError` where any concept note is an iCloud placeholder,
    `UnreadableNotesError` naming every note whose frontmatter does not parse, and
    `OSError` where the directory or a note cannot be read.
    """
    notes, placeholders = list_note_dir(root / CONCEPTS_DIR)
    if placeholders:
        raise EvictedNotesError(placeholders)
    concepts: list[ConceptNote] = []
    unreadable: list[str] = []
    for path in notes:
        file = path.relative_to(root).as_posix()
        fields = frontmatter(path.read_text(encoding="utf-8"))
        if fields is None:
            unreadable.append(file)
            continue
        concepts.append(ConceptNote(file=file, stem=path.stem, frontmatter=fields))
    if unreadable:
        raise UnreadableNotesError(unreadable)
    return concepts


def title_is_flagged(title: object, stem: str) -> bool:
    """Whether a wikilink written as `title` fails to reach the note named `stem`."""
    return (
        not isinstance(title, str)
        or title != stem
        or any(char in title for char in WIKILINK_SYNTAX)
    )


def flagged_titles(notes: Iterable[ConceptNote]) -> list[dict[str, str | None]]:
    """`{file, title}` for each flagged note; `title` is null where it is missing or not a string."""
    flagged: list[dict[str, str | None]] = []
    for note in notes:
        title = note.frontmatter.get("title")
        if title_is_flagged(title, note.stem):
            flagged.append({"file": note.file, "title": title if isinstance(title, str) else None})
    return flagged


def _tags(value: object) -> list[str]:
    """The tag strings a `tags` value carries, whichever YAML form wrote them."""
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


class TagCensus(TypedDict):
    """Tag use, most used first then by name, and the notes that carry no tag."""

    counts: dict[str, int]
    untagged: list[str]


def tag_census(notes: Iterable[ConceptNote]) -> TagCensus:
    """The facet-tag census over `notes`."""
    counts: Counter[str] = Counter()
    untagged: list[str] = []
    for note in notes:
        tags = _tags(note.frontmatter.get("tags"))
        if not tags:
            untagged.append(note.file)
        counts.update(tags)
    ordered = dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
    return {"counts": ordered, "untagged": untagged}
