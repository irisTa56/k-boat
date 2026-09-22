"""`kboat-note list` — one note folder's frontmatter, read so a skill does not.

A skill step that reads a note folder or one note by slug asks this rather than
globbing, listing, or opening files itself, so the agent reads only the notes and
fields it needs and never parses frontmatter by eye. The folder is listed through
`scan_required_dir`, so what the answer leaves out is always in `anomalies`: a
folder that is absent, not a directory, or refused is one entry under its own
name (and exit 1, the folder being in the vault's required set), each note iCloud
evicted is one under its placeholder's path, a note that could not be read or
parsed is one under its own, and so is each field of a note it would have shown
that the note holds in a shape the frontmatter reader does not model or names on
more than one line — what `kboat-validate` reports as `missing_field` and
`repeated_key` for such a field, and nothing further.

A slug is answered after that same listing, since an absent folder makes every
name in it read as free. A listed `<slug>.md` answers it outright. Otherwise the
volume is asked, as the writer asks it (`name_occupied`), in the order
`kboat-vault-conventions` gives: anything at `<slug>.md` first, then the
placeholder beside it, and only a name holding neither is absent. The listing
alone would disagree with the writer on a volume that folds case, APFS's
default: there `B0ABC.md` holds the slug `b0abc` for `lstat` and the write, and
an exact match over the listing would answer that no note does. What the probe
finds is then shown as the listed entry it is. Anything at the name is read, so
a directory or a dangling symlink there fails that read and is reported as such.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from kboat.cli import scan_required_dir
from kboat.frontmatter import (
    NOTE_READ_ERRORS,
    Value,
    named_key,
    parse_entries,
    parse_frontmatter,
    repeated_keys,
)
from kboat.io_utils import icloud_placeholder, name_occupied
from kboat.schema import BY_TYPE, DIR_BY_TYPE

_UNMODELLED = "field not readable: {} is held in a shape the frontmatter reader does not model"
_REPEATED = "field named on more than one line: {}, of which the answer holds the last"


def _at_slug(
    vault: Path,
    folder: str,
    slug: str,
    found: list[Path],
    anomalies: list[dict[str, str]],
) -> tuple[list[Path], list[dict[str, str]]]:
    """The listed note, or the listed placeholder's entry, that holds `slug`."""
    name = f"{slug}.md"
    exact = [p for p in found if p.name == name]
    if exact:
        return exact, []
    target = vault / folder / name
    stub = icloud_placeholder(target)
    try:
        if name_occupied(target):
            held = [p for p in found if p.name.casefold() == name.casefold()]
            unshown = f"{name} is held, but by no name this listing shows"
            return held, [] if held else [{"path": f"{folder}/{name}", "error": unshown}]
        if name_occupied(stub):
            rel = stub.relative_to(vault).as_posix().casefold()
            return [], [a for a in anomalies if a["path"].casefold() == rel]
    except OSError as exc:
        return [], [{"path": f"{folder}/{name}", "error": str(exc)}]
    return [], []


def list_notes(
    vault: Path,
    note_type: str,
    *,
    slug: str | None = None,
    fields: Sequence[str] = (),
    flagged: Sequence[str] = (),
) -> tuple[dict[str, object], bool]:
    """The `{notes, anomalies, counts}` report, and whether the folder could not be read.

    `slug` narrows the answer to that one name; `flagged` keeps only the notes whose
    every named field is `true`; `fields` cuts each note's frontmatter down to those
    keys (a key the note does not hold stays out).

    A note nobody could read cannot be shown to fail a filter, so it is reported
    whatever the filter was. So is a flagged field held in a shape the reader does
    not model, or named twice, since that note may be one the filter keeps. Any
    other such field is
    reported only where the answer would have shown it: on a note the filter
    keeps, and among `fields` where they are given, or the schema's where not.
    """
    folder = DIR_BY_TYPE[note_type]
    shown_fields = set(fields) if fields else set(BY_TYPE[note_type].field_names())
    found, anomalies, unread = scan_required_dir(vault, folder)
    if slug is not None and not unread:
        found, anomalies = _at_slug(vault, folder, slug, found, anomalies)
    notes: list[dict[str, object]] = []
    for path in found:
        rel = path.relative_to(vault).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            entries = parse_entries(text)
        except NOTE_READ_ERRORS as exc:
            anomalies.append({"path": rel, "error": str(exc)})
            continue
        # The two ways `fm` misstates a field, which `kboat-validate` reports as a
        # `missing_field` and a `repeated_key`: held in a shape the scanner does
        # not model (a block scalar, a nested mapping, a key outside its grammar,
        # which it names none for), so `fm` reads as a note without it; or named
        # on more than one line, so `fm` holds whichever came last.
        unmodelled = {
            key
            for e in entries
            if not e.modelled and (key := e.key or named_key(e.lines[0])) and key not in fm
        }
        repeated = set(repeated_keys(text))
        kept = all(fm.get(flag) is True for flag in flagged)
        asked = shown_fields | set(flagged) if kept else set(flagged)
        anomalies.extend(
            {"path": rel, "error": message.format(key)}
            for message, keys in ((_UNMODELLED, unmodelled), (_REPEATED, repeated))
            for key in sorted(keys & asked)
        )
        if not kept:
            continue
        shown: dict[str, Value] = {k: fm[k] for k in fields if k in fm} if fields else fm
        notes.append({"slug": path.stem, "path": rel, "frontmatter": shown})
    # `notes` last: a whole-folder report runs to hundreds of kilobytes, and a
    # caller shown only its head must still meet what it could not read.
    return {
        "anomalies": anomalies,
        "counts": {"notes": len(notes), "anomalies": len(anomalies)},
        "notes": notes,
    }, unread
