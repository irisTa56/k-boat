"""Classify a concept note's `## Observations` section into the shape its writer branches on.

A concept note accretes across readings, and kboat-distill divides its claims into
`###` groups once it carries more than one insight. A new group owes the note one
extra edit where the claims already there carry no heading, and this module answers
the question that turns on -- does the section carry any `###` group at all? The
format it classifies is specified by the `kboat-notes` skill, "Reading groups".

It is not a report on whether *every* claim in the section is under a heading. A
section whose earliest claims sit bare above its first `###` -- which a half-landed
append leaves behind -- answers `GROUPED`, and the writer reads that state off the
text rather than out of this record.

It answers that and nothing else. It is not a gate on whether the note can be
written to: `edit_note` resolves its own anchors and hands back a failure it cannot
resolve as part of its own result, which the writer reads and records, so a second
opinion here would only refuse appends that would have landed.

The scan mirrors basic-memory's own section matcher (`services/note_preparation.py`
`_markdown_heading_level`, `_fence_marker`, `_fenced_code_line_flags`, as of 0.23.0),
because the question is not what a markdown parser sees but what `edit_note`'s
anchors resolve to when the writer inserts. A heading inside a fenced code block is
an anchor to neither.
"""

from __future__ import annotations

OBSERVATIONS_SECTION = "## Observations"
RELATIONS_SECTION = "## Relations"

#: Level of the heading that opens a reading group. `####` and deeper are a
#: group's own internal structure, not another group.
GROUP_HEADING_LEVEL = 3

FLAT = "flat"
GROUPED = "grouped"


def _heading_level(line: str) -> int | None:
    """The ATX heading level of `line`, or `None` where it is not a heading."""
    indent = len(line) - len(line.lstrip(" "))
    if indent > 3:
        return None
    candidate = line[indent:]
    if not candidate.startswith("#"):
        return None
    level = len(candidate) - len(candidate.lstrip("#"))
    if level > 6:
        return None
    rest = candidate[level:]
    return level if not rest or rest.startswith((" ", "\t")) else None


def _fence_marker(line: str) -> tuple[str, int, str] | None:
    """`(marker char, run length, trailing text)` where `line` is a fence, else `None`."""
    indent = len(line) - len(line.lstrip(" "))
    if indent > 3:
        return None
    candidate = line[indent:]
    if not candidate or candidate[0] not in ("`", "~"):
        return None
    marker = candidate[0]
    marker_length = len(candidate) - len(candidate.lstrip(marker))
    if marker_length < 3:
        return None
    return marker, marker_length, candidate[marker_length:]


def _fenced_flags(lines: list[str]) -> list[bool]:
    """Per line, whether it belongs to a fenced code block (the fence lines included).

    A backtick fence whose info string carries a backtick does not open one, and a
    fence closes only on the same character at least as long and with nothing after
    it -- both CommonMark, and both what the editor applies.
    """
    flags: list[bool] = []
    open_marker: str | None = None
    open_length = 0
    for line in lines:
        marker = _fence_marker(line)
        if open_marker is None:
            if marker is None:
                flags.append(False)
                continue
            marker_char, marker_length, suffix = marker
            if marker_char == "`" and "`" in suffix:
                flags.append(False)
                continue
            flags.append(True)
            open_marker, open_length = marker_char, marker_length
            continue
        flags.append(True)
        if marker is not None:
            marker_char, marker_length, suffix = marker
            if marker_char == open_marker and marker_length >= open_length and not suffix.strip():
                open_marker, open_length = None, 0
    return flags


def _section_line(lines: list[str], fenced: list[bool], section: str, start: int = 0) -> int | None:
    """Index of the first unfenced `section` anchor at or after `start`, matched as the editor does."""
    return next(
        (i for i in range(start, len(lines)) if not fenced[i] and lines[i].strip() == section),
        None,
    )


def classify(text: str) -> str | None:
    """`GROUPED` where the note's Observations section already carries `###` groups, else `FLAT`.

    `None` where `text` carries no `## Observations` heading at all. That is not a
    third shape but a refusal to answer: an omitted redirect, an empty file and
    `read_note`'s not-found document all arrive looking like this, and `FLAT` would
    be an answer about a note the tool never saw. It is the one wrong answer that
    writes -- on a note that is really grouped, the writer's flat branch mints a
    heading over the real first group, the anchor is there so the edit succeeds, and
    nothing in the run or any later run reports it.

    A note that has the section and nothing in it is still `FLAT`: there is simply
    nothing for a new group to wrap.
    """
    lines = text.split("\n")
    fenced = _fenced_flags(lines)
    start = _section_line(lines, fenced, OBSERVATIONS_SECTION)
    if start is None:
        return None
    relations = _section_line(lines, fenced, RELATIONS_SECTION, start + 1)
    end = len(lines) if relations is None else relations
    grouped = any(
        not fenced[i] and _heading_level(lines[i]) == GROUP_HEADING_LEVEL
        for i in range(start + 1, end)
    )
    return GROUPED if grouped else FLAT
