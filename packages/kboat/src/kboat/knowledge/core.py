"""The concept-note shape check.

The knowledge root is not the vault, so this is not `kboat-validate`'s job: a
concept note carries no frontmatter schema to check, and what goes wrong with
one is structural — whether both sections are there and in order, and whether
every observation sits in a reading group its `[source]` line closes.

`kboat-notes` "Reading groups in a multi-source note" defines that shape and
`kboat-curate` reports what this finds. The check itself is deterministic and
purely mechanical, so it lives here rather than as a scan an agent re-derives
from prose (the root `CLAUDE.md`, "What this repo is").

`CODES` below is the whole of what a code means: its `disposition`, which is what
a reader does about it, and its `repair`, which is what a human does. Every
finding carries both, so a caller branches on the disposition and prints the
repair rather than restating either — a code list written out in a skill is one
more place to keep in step, and the places went out of step three times before
this table existed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

OBSERVATIONS = "## Observations"
RELATIONS = "## Relations"

_SECTION = re.compile(r"^## ")
# Two questions, one predicate. Only a level-3 heading opens a reading group, which
# is what `kboat-notes` writes and what the group count means; the *text* of any
# heading is an inline the graph reads, so the content check takes every level.
# Both come from `_heading` below rather than from a pattern of this module's own —
# a hand-rolled one disagreed with `_heading_level` three lines from it, missing a
# heading written with two spaces or a tab, missing levels 1 and 2 entirely, and
# taking a closing `#` run as part of the text.
GROUP_LEVEL = 3
_WIKILINK_IN = re.compile(r"\[\[[^\[\]]+\]\]")
# Basic Memory's own predicate, mirrored rather than approximated: what this module
# is for is knowing what the graph holds, so a rule of our own — narrower *or* wider
# — reports on a base that does not exist. Every line below is `is_observation` and
# `_observation_category_match` from `basic_memory/markdown/plugins.py`, read out of
# the installed 0.23.0 (the version whose `edit_note` carries `replace_subsections`).
#
# Mirroring includes the parts that look like mistakes. `_LINK_ONLY`'s `.*?` against
# a `$` anchor backtracks, so it excludes any body opening with `[`, holding `](`
# and ending in `)` — a claim citing its source inline among them. Narrowing it
# would make this check see claims the graph does not hold, which is the failure in
# the other direction: a `[source]` line written that way closes nothing *there*
# either, so `unclosed_claims` on it is the true answer and not a false alarm.
#
# `is_observation` has changed under this module once already (0.23.0 added the
# timestamp and task-marker rejections below), so re-read it rather than trusting
# this comment when the pin moves.
_BULLET = re.compile(r"^[-*+] (.*)$")
_CATEGORY = re.compile(r"^\[([^\[\]()]+)\]\s+(.+)")
_TASK = ("[ ]", "[x]", "[-]")
# A category that is purely a clock value is a transcript timecode, and a
# one-character non-alphanumeric (or `x`/`X`) one is the extended task vocabulary
# `[/]`, `[>]`, `[?]`. Basic Memory rejects both; a check that did not would count
# one junk claim per spoken line.
_TIMESTAMP_VALUE = r"\d{1,3}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?"
_TIMESTAMP_CATEGORY = re.compile(rf"^{_TIMESTAMP_VALUE}(?:\s+-\s+{_TIMESTAMP_VALUE})?$")
_LINK_ONLY = re.compile(r"^\[.*?\]\(.*?\)$")
_WIKILINK_ONLY = re.compile(r"^\[\[.*?\]\]$")


def _bullet_body(line: str) -> str | None:
    """The text of a list item Basic Memory would read as an observation, if it is one."""
    match = _BULLET.match(line)
    if not match:
        return None
    body = match.group(1).strip()
    return body if _indexed_inline(body) else None


def _is_task_marker_category(category: str) -> bool:
    """The extended checkbox vocabulary, which shares the category's bracket shape."""
    return len(category) == 1 and (category in {"x", "X"} or not category.isalnum())


def _category_match(text: str) -> re.Match[str] | None:
    """`[category] content`, rejecting the timestamp and task-marker shapes."""
    match = _CATEGORY.match(text)
    if not match:
        return None
    category = match.group(1).strip()
    if _TIMESTAMP_CATEGORY.match(category) or _is_task_marker_category(category):
        return None
    return match


def _indexed_inline(text: str) -> bool:
    """Whether Basic Memory would read this inline as an observation — `is_observation`.

    One function for every inline, because that is how `observation_rule` works: it
    walks all inline tokens, so a heading's text takes the same exclusions a list
    item's does. Split across two paths, the exclusions reached the bullets and not
    the headings, and a `### [ ] a draft heading` — which the graph reads nothing
    from — came back `heading_parses_as_content`, blocking the note.
    """
    text = text.strip()
    if not text or text.startswith(_TASK):
        return False
    if _LINK_ONLY.match(text) or _WIKILINK_ONLY.match(text):
        return False
    return bool(_category_match(text) or any(part.startswith("#") for part in text.split()))


def _is_source(body: str) -> bool:
    return body.startswith("[source]")


@dataclass(frozen=True)
class Code:
    """What one code means: who acts on it, and what they do."""

    disposition: str
    repair: str


#: What a reader does about a finding. Four values, because there are four answers:
#: the report is not about the knowledge base at all; this one note cannot be
#: written into; nothing is owed and the next run settles it; or the check does not
#: cover the file at all, so no source is being aborted by it whatever else is true —
#: which is not the same as nothing being owed, a concept note that has left
#: `concepts/` being real work its own `repair` names. The last is its own answer rather than a `blocks` with a repair
#: saying to leave it — three rounds tried to say that in prose and the disposition
#: kept telling the curator to work it first.
BASE = "base"
BLOCKS = "blocks"
PASSES = "passes"
UNCOVERED = "uncovered"

CODES: dict[str, Code] = {
    "missing_section": Code(
        BLOCKS,
        "Add the missing heading, empty where there is nothing to put under it. "
        "A concept note carries `## Observations` then `## Relations` (kboat-notes).",
    ),
    "duplicate_section": Code(
        BLOCKS,
        "Drop the duplicate. A section insert raises on a repeated heading exactly as on a missing one.",
    ),
    "sections_out_of_order": Code(
        BLOCKS,
        "Put the two sections back together, `## Observations` then `## Relations`, with nothing between "
        "them. A reading group is inserted *before* `## Relations`, anchored on that line — so with the "
        "order reversed it lands above `## Observations` and with a third section between them it lands "
        "inside that one, in neither case in the section it belongs to, while Basic Memory keeps indexing "
        "the observations wherever they sit. Whatever stood between them goes after `## Relations`.",
    ),
    "unclosed_claims": Code(
        BLOCKS,
        "Repair this one first: find where the unprovenanced claims came from, or drop them. The refusal "
        "is what keeps it from worsening — a distillation allowed to write into the note would close "
        "those claims with *its* `[source]` line, after which the base would assert a source supports "
        "claims it never made and the note would check clean for good.",
    ),
    "heading_parses_as_content": Code(
        BLOCKS,
        "Rewrite the heading so it carries none of the three: a `#`-prefixed word, a leading `[...]`, or a "
        "`[[wikilink]]` (kboat-notes). As it stands the graph holds a claim with no `[source]` closing it, "
        "or an edge no bullet declared — and a heading is where nobody looks for either.",
    ),
    "claim_outside_observations": Code(
        BLOCKS,
        "The line has to stop reading as an observation, or come to sit in a reading group a `[source]` "
        "line closes — so reword it, move it under `## Observations`, or take it out. Which one fits "
        "depends on what the line is: a claim moves or goes, while a `## Relations` edge belongs where "
        "it stands and only its context needs freeing of a `#`-prefixed word (kboat-notes) — moving that "
        "one lands the note on `unclosed_claims` and taking it out drops a real edge. The graph reads an "
        "observation out of any inline in the file, not out of `## Observations` alone, so this one is "
        "in the base already — carrying no `[source]` line and belonging to no reading. `append` puts a "
        "claim here, which is why the writer is told never to use it.",
    ),
    "heading_group_mismatch": Code(
        BLOCKS,
        "Head each reading group per kboat-notes, and drop any heading left over. A group is one run "
        "however many `[source]` lines close it.",
    ),
    "empty_observations": Code(
        PASSES,
        "Nothing to repair: the next distillation fills it as the note's first group. A note that stays "
        "empty across several passes is one to question.",
    ),
    "spare_headings": Code(
        BLOCKS,
        "Take off every heading that heads nothing, and leave the rest alone: what governs is the note's "
        "own rule, not a count — a note holding more than one reading group gives each exactly one "
        "heading, and a lone group takes none (kboat-notes). Where the groups this leaves are not each "
        "headed, `heading_group_mismatch` is reported alongside this and names that work. Not the "
        "interrupted conversion below: the next append does not absorb a heading standing below its "
        "group, it makes the note `heading_group_mismatch` — so an unattended pass told to go ahead "
        "would leave a note every later source aborts on.",
    ),
    "lone_group_headed": Code(
        PASSES,
        "Leave it. One heading over one group is what a conversion interrupted between its two writes "
        "leaves behind, and the next append turns it into an ordinary two-group note. If it persists "
        "across several passes no append is coming, and the heading comes off.",
    ),
    "note_outside_concepts": Code(
        UNCOVERED,
        "Move it into `concepts/`, or into `meta/` if it is a hub or vocabulary note and not a concept "
        "note at all. Nothing reads the shape of a note outside those. A file that is no part of the base "
        "— a repository README, a folder index — has no third folder to go to and none is wanted: leave "
        "it, and read this as the report saying what the check does not cover rather than as work.",
    ),
    "no_knowledge_root": Code(
        BASE,
        "The root is wrong — a stale `KBOAT_KNOWLEDGE_PATH`, or a base that moved — not the base. Fix "
        "the root: without this code the same run would report zero notes and zero findings, which is "
        "what a clean base looks like.",
    ),
    "no_concepts_dir": Code(
        PASSES,
        "Nothing to repair: the root is there and no concept note has been written into it yet. The "
        'folder is made by the first `write_note(directory="concepts")`, so a pass that refused to run '
        "over its absence would be refusing the one thing that creates it.",
    ),
    "unreadable_dir": Code(
        BASE,
        "Fix the access and run it again. No count in this report describes the whole base.",
    ),
    "unreadable_note": Code(
        BLOCKS,
        "Fix the access, or the encoding: the note is there and this check could not read it, so nothing "
        "knows its shape.",
    ),
    "icloud_placeholder": Code(
        BLOCKS,
        "Download the note back from iCloud. It was evicted to a placeholder, so nothing knows its shape.",
    ),
}


@dataclass(frozen=True)
class ShapeFinding:
    """One departure from the concept-note shape, in one note."""

    note: str
    code: str
    detail: str

    def __post_init__(self) -> None:
        # A code with no entry would reach a caller carrying no disposition to
        # branch on and no repair to print, so it fails here rather than there.
        if self.code not in CODES:
            raise ValueError(f"unknown code: {self.code}")

    def to_json(self) -> dict[str, str]:
        meaning = CODES[self.code]
        return {
            "note": self.note,
            "code": self.code,
            "detail": self.detail,
            "disposition": meaning.disposition,
            "repair": meaning.repair,
        }


def _fence_marker(line: str) -> tuple[str, int, str] | None:
    """`(marker, length, info)` where this line opens or closes a fence, else `None`."""
    indent = len(line) - len(line.lstrip(" "))
    if indent > 3:
        return None
    candidate = line[indent:]
    if not candidate or candidate[0] not in ("`", "~"):
        return None
    marker = candidate[0]
    length = len(candidate) - len(candidate.lstrip(marker))
    if length < 3:
        return None
    return marker, length, candidate[length:]


def _fenced(lines: list[str]) -> list[bool]:
    """Per line, whether it sits inside a fence.

    `_fence_marker` and this are `note_preparation.py`'s `_fence_marker` and
    `_fenced_code_line_flags`, mirrored for the reason `_bullet_body` mirrors
    `is_observation`: Basic Memory gates its own anchor match on these, and a rule
    of our own reports on a document it reads differently. Approximating them with
    "a line starting with ```" gets four things wrong — `~~~`, a marker indented
    past three spaces, a backtick fence whose info string holds a backtick, and a
    closing fence shorter than its opener — and the first of those turns a note
    Basic Memory writes into happily into a `blocks` finding.

    It matters because `kboat-notes` prescribes a fenced block for a multi-line
    formula without naming the marker, and because markdown-it hands a fence to
    the parser as one `fence` token: nothing inside one is a heading or a claim.
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
                open_marker = None
    return flags


def _section_lines(lines: list[str], heading: str, fenced: list[bool]) -> list[int]:
    """Every line index whose whole line is `heading`, fences excluded.

    `strip`, not `rstrip`: Basic Memory anchors on `line.strip() == header.strip()`
    (`note_preparation.py`), so an indented `  ## Relations` is an anchor it writes
    against happily — and reporting it missing would block a note the writer can
    use.

    All of them rather than the first: `edit_note`'s section inserts raise on a
    duplicated anchor exactly as they do on a missing one, so a second copy is a
    state the writer cannot act on and this check has to see.
    """
    return [i for i, line in enumerate(lines) if not fenced[i] and line.strip() == heading]


def _heading_level(line: str) -> int | None:
    """The heading level of `line`, or `None` — `note_preparation.py`'s predicate.

    Mirrored like `_fence_marker` beside it, and for the same reason: this is what
    ends a section upstream, so a boundary of our own reads a document Basic Memory
    reads differently. Matching `^## ` alone missed an indented `## Relations` — an
    anchor `_section_lines` deliberately accepts — and ran the observations scan on
    through the relation bullets below it.
    """
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


def _heading(line: str) -> tuple[int, str] | None:
    """`(level, text)` for an ATX heading, the text as markdown-it takes it.

    The whitespace run after the hashes is a delimiter however long it is, and a
    closing run of `#` preceded by whitespace is a marker rather than content.
    """
    level = _heading_level(line)
    if level is None:
        return None
    rest = line.lstrip(" ")[level:].strip()
    closed = rest.rstrip("#")
    if closed != rest and (not closed or closed[-1].isspace()):
        rest = closed.strip()
    return level, rest


def _block(lines: list[str], start: int, fenced: list[bool]) -> tuple[list[str], int]:
    """The lines under the section heading at `start`, and the index they stop at.

    A section ends at the next heading of the same level or higher, which is what
    upstream computes; a heading inside a fence is not a heading, so it ends nothing.
    """
    # `_section_lines` matched this line as a whole-line `## ` heading, so the level
    # is always there; no fallback guards anything reachable.
    level = _heading_level(lines[start])
    assert level is not None
    out: list[str] = []
    for i in range(start + 1, len(lines)):
        if not fenced[i]:
            here = _heading_level(lines[i])
            if here is not None and here <= level:
                return out, i
        out.append("" if fenced[i] else lines[i])
    return out, len(lines)


def _scan(block: list[str]) -> tuple[int, int, bool, int, list[str], int]:
    """`(groups, headings, saw_bullet, unclosed, bad_headings, spare)` for one block.

    `headings` counts only the headings something followed; `spare` counts the rest.

    A group is counted by the run of `[source]` lines that closes it, never by
    the headings above it: counting headings is what makes a note nobody ever
    converted look like the single flat group it is allowed to be.

    `bad_headings` is the headings whose own text the graph reads as content.
    Heading text is an inline like any other, so a `#`-prefixed word or a leading
    `[...]` makes the heading an observation and a `[[wikilink]]` makes it an
    edge — the three `kboat-notes` forbids there. They are worth their own answer
    because this change is what put headings in these notes at all, and a heading
    is the one place a reader checking provenance would not think to look.
    """
    groups = headings = unclosed = spare = 0
    saw_bullet = open_run = was_source = pending = False
    bad_headings: list[str] = []
    for line in block:
        any_heading = _heading(line)
        if any_heading:
            # Only a group heading ends a run. A group boundary is what `kboat-notes`
            # writes as `###`, and a deeper heading is an aside inside a group — no
            # rule forbids one, and treating it as a boundary reported a group whose
            # `[source]` line sits two lines below as unclosed. The text of every
            # level is still read, since the graph reads every heading.
            if any_heading[0] == GROUP_LEVEL:
                # A heading that never met a bullet heads nothing. Counting it all
                # the same let a heading standing *below* its group read as that
                # group's own, so the next append cemented a note whose first group
                # carried none — the inference from bullet position this whole
                # change exists to remove, asserted wrongly and for good.
                if pending:
                    spare += 1
                pending = True
                if open_run:
                    unclosed += 1
                    open_run = False
                # And the run of `[source]` lines ends here too, or the next group's
                # provenance would read as a continuation of the last one's and the
                # two would count as one.
                was_source = False
            text = any_heading[1]
            if _indexed_inline(text) or _WIKILINK_IN.search(text):
                bad_headings.append(text)
            continue
        body = _bullet_body(line)
        if body is None:
            continue
        saw_bullet = True
        if pending:
            headings += 1
            pending = False
        if _is_source(body):
            # A `[source]` run closes a group even with no claims above it: a reading
            # that contributed nothing is still a reading, and counting it as no
            # group at all left such a note matching no branch below — clean until
            # the writer put a heading over it and made the note `spare_headings`.
            if not was_source:
                groups += 1
            open_run = False
        else:
            open_run = True
        was_source = _is_source(body)
    if open_run:
        unclosed += 1
    if pending:
        spare += 1
    return groups, headings, saw_bullet, unclosed, bad_headings, spare


def _stray_claims(
    note: str, lines: list[str], fenced: list[bool], start: int, end: int
) -> list[ShapeFinding]:
    """Every indexed inline outside the observations block, which the graph holds anyway.

    `observation_rule` walks the whole document with no section awareness, so a
    claim under a later section — or appended past `## Relations`, which is what
    `append` does — is in the base with nothing closing it, while a scan reading
    only `## Observations` calls the note clean.
    """
    out: list[ShapeFinding] = []
    for i, line in enumerate(lines):
        if fenced[i] or start <= i < end:
            continue
        heading = _heading(line)
        text = heading[1] if heading else _bullet_body(line)
        if not text:
            continue
        if heading:
            # Its repair is the heading's own. `claim_outside_observations` says to
            # move it under `## Observations`, which for a heading produces exactly
            # what `heading_parses_as_content` blocks on — so the human does the work
            # the report named and the note blocks on the next code instead.
            if _indexed_inline(text) or _WIKILINK_IN.search(text):
                out.extend(_heading_findings(note, [text]))
        elif _indexed_inline(text):
            out.append(
                ShapeFinding(
                    note, "claim_outside_observations", f"outside a reading group: {text!r}"
                )
            )
    return out


def _heading_findings(note: str, bad_headings: list[str]) -> list[ShapeFinding]:
    """One finding per heading whose own text the graph reads as content."""
    return [
        ShapeFinding(
            note, "heading_parses_as_content", f"the graph reads this heading as content: {h!r}"
        )
        for h in bad_headings
    ]


def check_concept_note(text: str, note: str) -> list[ShapeFinding]:
    """Every way `text` departs from the concept-note shape, as findings for `note`.

    All of them, not the first: a note can be both out of order and unclosed,
    and reporting only the lesser would leave the worse one masked until whoever
    ran this happened to run it again.
    """
    lines = text.splitlines()
    fenced = _fenced(lines)
    findings: list[ShapeFinding] = []
    obs = _section_lines(lines, OBSERVATIONS, fenced)
    rel = _section_lines(lines, RELATIONS, fenced)

    for heading, found in ((OBSERVATIONS, obs), (RELATIONS, rel)):
        if not found:
            findings.append(ShapeFinding(note, "missing_section", f"no `{heading}` section"))
        elif len(found) > 1:
            findings.append(
                ShapeFinding(
                    note,
                    "duplicate_section",
                    f"`{heading}` appears {len(found)} times; a section insert raises on that",
                )
            )
    if not obs:
        return findings
    if rel and rel[0] < obs[0]:
        findings.append(
            ShapeFinding(
                note,
                "sections_out_of_order",
                f"`{RELATIONS}` stands above `{OBSERVATIONS}`, so a reading group inserted "
                f"before it lands outside both sections",
            )
        )

    block, block_end = _block(lines, obs[0], fenced)
    # The insert anchors on the `## Relations` line, so the two sections have to be
    # adjacent as well as ordered: a third section between them takes the group.
    if rel and obs[0] < rel[0] and block_end < rel[0]:
        findings.append(
            ShapeFinding(
                note,
                "sections_out_of_order",
                f"a section stands between `{OBSERVATIONS}` and `{RELATIONS}`, so a reading "
                f"group inserted before `{RELATIONS}` lands inside it",
            )
        )
    groups, headings, saw_bullet, unclosed, bad_headings, spare = _scan(block)
    findings.extend(_stray_claims(note, lines, fenced, obs[0], block_end))
    findings.extend(_heading_findings(note, bad_headings))
    if not saw_bullet:
        # The one exclusion left, and it is about the *counts*: with no bullets there
        # are no groups, so nothing below could say anything true about this note.
        # The heading findings are not among them — a heading puts its own claim in
        # the graph whether or not a bullet ever follows, and suppressing them here
        # is what lets the next pass write a `[source]` line that closes a claim no
        # source made, on a note this check called `passes`.
        findings.append(ShapeFinding(note, "empty_observations", "no observations yet"))
        return findings
    if unclosed:
        findings.append(
            ShapeFinding(
                note,
                "unclosed_claims",
                f"{unclosed} run(s) of observations not closed by a `[source]` line, "
                f"so they carry no provenance",
            )
        )
    # `groups == 0` with headings counts as spare too: a heading with no group under
    # it is spare whatever the count, and leaving it off reported the note clean
    # until the next append made it `spare_headings` — after that write had landed.
    excess = spare + (max(0, headings - groups) if groups <= 1 else 0)
    if groups == 1 and headings == 1 and not spare:
        findings.append(
            ShapeFinding(
                note,
                "lone_group_headed",
                "one reading group carrying one heading; a lone group takes none",
            )
        )
    elif excess:
        findings.append(
            ShapeFinding(
                note,
                "spare_headings",
                f"{excess} heading(s) heading no reading group, over {groups} reading group(s)",
            )
        )
    # Not an `elif`: a spare heading and unheaded groups are separate work, and
    # suppressing the second left the report naming none of it — removing the
    # spare was then the whole repair, and the note came back `blocks`.
    if groups > 1 and headings != groups:
        findings.append(
            ShapeFinding(
                note,
                "heading_group_mismatch",
                f"{groups} reading groups, {headings} heading(s)",
            )
        )
    return findings
