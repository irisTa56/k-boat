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
written to: `bm_edit` resolves its own anchors and returns an error for one it cannot
resolve, which the writer reads and records, so a second
opinion here would only refuse appends that would have landed.

The scan reads the note as `bm_edit` does when it resolves an anchor (basic-memory's
`markdown/sections.py`): the body below the frontmatter, parsed by the same
markdown-it CommonMark preset, with a section running from its heading to the next
heading of the same level or higher. The question is not what the text looks like
line by line but what `bm_edit`'s anchors resolve to when the writer inserts, and a
parser answers that where a hand-kept line classifier drifts from it -- a setext
heading, a heading inside a list item or an HTML block, a fence its list item
closes.
"""

from markdown_it import MarkdownIt

from kboat.frontmatter import strip_frontmatter

#: The heading that opens the section, as level and text.
OBSERVATIONS_LEVEL = 2
OBSERVATIONS_TEXT = "Observations"

#: Level of the heading that opens a reading group. `####` and deeper are a
#: group's own internal structure, not another group.
GROUP_HEADING_LEVEL = 3

FLAT = "flat"
GROUPED = "grouped"

# basic-memory's own parser: the CommonMark preset with no plugins, since heading
# boundaries need none.
_MARKDOWN = MarkdownIt()


def _headings(body: str) -> list[tuple[int, str]]:
    """`(level, text)` of every heading in `body`, in document order."""
    tokens = _MARKDOWN.parse(body)
    return [
        (int(token.tag[1:]), tokens[index + 1].content.strip())
        for index, token in enumerate(tokens)
        if token.type == "heading_open"
    ]


def classify(text: str) -> str | None:
    """`GROUPED` where the note's Observations section already carries `###` groups, else `FLAT`.

    `None` where `text` carries no `## Observations` heading at all. That is not a
    third shape but a refusal to answer: an omitted redirect and an empty file both
    arrive looking like this, and `FLAT` would
    be an answer about a note the tool never saw. It is the one wrong answer that
    writes -- on a note that is really grouped, the writer's flat branch mints a
    heading over the real first group, the anchor is there so the edit succeeds, and
    nothing in the run or any later run reports it.

    A note that has the section and nothing in it is still `FLAT`: there is simply
    nothing for a new group to wrap.
    """
    headings = _headings(strip_frontmatter(text))
    start = next(
        (
            index
            for index, heading in enumerate(headings)
            if heading == (OBSERVATIONS_LEVEL, OBSERVATIONS_TEXT)
        ),
        None,
    )
    if start is None:
        return None
    for level, _ in headings[start + 1 :]:
        if level <= OBSERVATIONS_LEVEL:
            break
        if level == GROUP_HEADING_LEVEL:
            return GROUPED
    return FLAT
