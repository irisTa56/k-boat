"""`kboat-note list` — one note folder's frontmatter, read so a skill does not.

A skill step that reads a note folder or one note by slug asks this rather than
globbing, listing, or opening files itself, so the agent reads only the notes and
fields it needs and never parses frontmatter by eye. The folder is listed through
`scan_required_dir`, so what the answer leaves out is always in `anomalies`: a
folder that is absent, not a directory, or refused is one entry under its own
name (and exit 1, the folder being in the vault's required set), each note iCloud
evicted is one under its placeholder's path, a note that could not be read or
parsed is one under its own, and so is each field of a note it would have shown
that the note holds in a shape the frontmatter reader does not model.

A slug is answered from that same listing rather than by probing the name. The
listing is what tells an evicted note from an absent one in the order
`kboat-vault-conventions` gives: anything at `<slug>.md` comes first and is read
(a directory or a dangling symlink there fails that read and is reported as
such), a placeholder counts only where nothing is at the name (`evictions`), and
only a name the listing holds neither of is absent. Probing the name instead
would still need the folder listed, since an absent folder makes every name in
it read as free.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from kboat.cli import scan_required_dir
from kboat.frontmatter import NOTE_READ_ERRORS, Value, parse_entries, parse_frontmatter
from kboat.io_utils import icloud_placeholder
from kboat.schema import BY_TYPE, DIR_BY_TYPE

_UNMODELLED = "field not readable: {} is held in a shape the frontmatter reader does not model"


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
    not model, since that note may be one the filter keeps. Any other such field is
    reported only where the answer would have shown it: on a note the filter
    keeps, and among `fields` where they are given, or the schema's where not.
    """
    folder = DIR_BY_TYPE[note_type]
    shown_fields = set(fields) if fields else set(BY_TYPE[note_type].field_names())
    found, anomalies, unread = scan_required_dir(vault, folder)
    if slug is not None:
        name = f"{slug}.md"
        placeholder = icloud_placeholder(Path(folder) / name).as_posix()
        found = [p for p in found if p.name == name]
        anomalies = [a for a in anomalies if a["path"] in (folder, placeholder)]
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
        # A field held in a shape the scanner does not model (a block scalar, a
        # nested mapping) is absent from `fm`, which would otherwise read as a
        # note without it.
        unmodelled = {e.key for e in entries if not e.modelled and e.key and e.key not in fm}
        kept = all(fm.get(flag) is True for flag in flagged)
        reported = unmodelled & (shown_fields | set(flagged) if kept else set(flagged))
        anomalies.extend(
            {"path": rel, "error": _UNMODELLED.format(key)} for key in sorted(reported)
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
