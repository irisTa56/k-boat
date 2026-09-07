"""Fail if a markdown file's list shape and the prose in it disagree.

Stdlib-only by design, for the same reason `coverage_floor.py` is: it runs from
the `qa:md` gate and from CI's `markdown-lint` job, neither of which installs
anything to run it.

`rumdl` catches neither fault below: every line involved is well-formed markdown
line by line, and what is wrong about it is only which block it belongs to.
Hence a check of its own.

A **fold**. CommonMark's
[lazy continuation](https://spec.commonmark.org/0.31.2/#lazy-continuation-line)
lets a paragraph carry on across a line indented less than the block it began
in. Inside a nested list that turns an author's indentation into a lie:

    - outer item
      - deeper child
      text meant to continue the outer item

The last line sits at the outer item's content column, so it reads as the outer
item's prose. The deeper child's paragraph is still open and no blank line
closed it, so CommonMark appends the line to *that* paragraph instead, and the
rendered document says something the source does not. A blank line before it
closes the paragraph and the same line then lands where its indentation puts it.

That fault includes a line sitting at an item's own marker column rather than
under a deeper child: short of the content column, it too would land outside the
list if a blank line closed the paragraph, and laziness keeps it in the item. It
also includes a line back at the margin, which no item's content column can
account for -- which is why the fold is reported even where the second fault
below cannot see it.

**Prose inside a list item**. A paragraph and a list are exclusive: a list item
is the line its marker is on, and an item that needs prose under it is a section
with a heading instead. So a non-blank line sitting at or past an open item's
content column is prose that item swallowed, blank line before it or not:

    - outer item

      a second paragraph belonging to that item

Where both faults describe the same line, it is reported as a fold, the more
specific reading of the two.

The block starts that interrupt an open paragraph -- a blockquote, an HTML
block, a thematic break -- are exempt from the fold, which is a claim about
where CommonMark puts a line and would be false for them. They are not exempt
from prose inside an item, which claims only that a block sits inside one, and
that is as true of them as of a paragraph.

The scan is otherwise narrow, and where it cannot decide it does not report: it
gates commits, so a false positive costs more than a miss. Two consequences
worth naming. A fenced block is never scanned and so never reported, whatever it
is nested in. And a heading closes every open item, so prose under a heading
that is itself indented inside an item goes unreported.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# A list marker plus the whitespace after it. The match end is the item's
# content column, which is what decides whether a later line is inside it.
_MARKER = re.compile(r"^(\s*)(?:[-*+]|\d{1,9}[.)])(\s+)")
_FENCE = re.compile(r"^(\s*)(```+|~~~+)")
_HEADING = re.compile(r"^(\s*)#{1,6}(\s|$)")
# The other block starts that interrupt an open paragraph rather than continuing
# it, so a line beginning with one is never folded whatever its indentation.
# Scanning inside a blockquote is out of scope -- this repo has none under a list
# item, and reporting the wrong line inside one would block a correct commit.
_BLOCKQUOTE = re.compile(r"^(\s*)>")
# Deliberately every `<`, not the six HTML block kinds that really interrupt a
# paragraph: telling those from the seventh, which does not, needs the tag-name
# list. The cost is missing a fold on a line that opens with an autolink; the
# alternative is reporting a comment or a block tag that is not one at all.
_HTML = re.compile(r"^(\s*)<")
# Four columns past the enclosing block's content column, with no paragraph open,
# starts an indented code block. Nothing continues one lazily.
_CODE_INDENT = 4
# CommonMark caps an item's content column: more than this many spaces after the
# marker and the content column is the marker's end plus one, the rest being an
# indented code block inside the item.
_MAX_MARKER_SPACES = 4
# All three spellings CommonMark gives a thematic break. `---` is also a setext
# underline where a paragraph is open above it, and both readings end that
# paragraph, so either way nothing is folded past this line. Matched before the
# marker, the precedence CommonMark gives it: `- - -` is a break, not an item.
_THEMATIC_BREAK = re.compile(r"^\s*(?:(?:\*\s*){3,}|(?:_\s*){3,}|(?:-\s*){3,})$")
_FRONTMATTER = "---"

# Exit 2, "the input isn't what this script expects", never exit 1, "a fault is
# there" -- the same split `coverage_floor.py` draws.
_EXIT_FAULT = 1
_EXIT_MALFORMED = 2

FOLD = "fold"
PROSE = "prose"

_REMEDY = {
    FOLD: (
        "is folded into the item at column {item_column} -- put a blank line "
        "before it or indent it to that item"
    ),
    PROSE: (
        "is prose inside the item at column {item_column} -- a list item is the "
        "line its marker is on, so fold this onto that line or give the item a "
        "heading of its own"
    ),
}


@dataclass(frozen=True)
class Report:
    """One reported line: which fault, where it is, and which item claims it."""

    kind: str
    path: str
    line_no: int
    indent: int
    item_column: int
    text: str

    def __str__(self) -> str:
        remedy = _REMEDY[self.kind].format(item_column=self.item_column)
        return (
            f"{self.path}:{self.line_no}: line at column {self.indent} {remedy}"
            f"\n    {self.text.strip()}"
        )


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _content_column(marker: re.Match[str]) -> int:
    """The column an item's content starts at, capped as CommonMark caps it."""
    spaces = len(marker.group(2))
    if spaces > _MAX_MARKER_SPACES:
        return marker.end() - spaces + 1
    return marker.end()


def _frontmatter_end(lines: list[str]) -> int:
    """How many opening lines to skip, so YAML `-` lines are not list markers."""
    if not lines or lines[0].strip() != _FRONTMATTER:
        return 0
    for i, line in enumerate(lines[1:], 2):
        if line.strip() == _FRONTMATTER:
            return i
    return 0


def _inside_an_item(
    stack: list[int], path: Path, line_no: int, indent: int, line: str
) -> list[Report]:
    """Close the items this line outdents, and report the one it lands inside.

    Mutates `stack`. What survives the unwinding has a content column no deeper
    than this line's indentation, so an innermost survivor is an item the line
    sits at or past the content column of -- the whole of the second fault.
    """
    while stack and stack[-1] > indent:
        stack.pop()
    if not stack:
        return []
    return [Report(PROSE, str(path), line_no, indent, stack[-1], line)]


def scan(path: Path) -> list[Report]:
    """Return every misshapen line in one markdown file."""
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    reports: list[Report] = []

    # Content columns of the list items currently open, shallowest first.
    stack: list[int] = []
    # Whether a paragraph is open, which is what lazy continuation needs.
    para_open = False
    fence: str | None = None

    # A skill file opens with YAML frontmatter, whose `-` lines would otherwise
    # read as list markers.
    start = _frontmatter_end(lines)

    for line_no, line in enumerate(lines[start:], start + 1):
        fence_match = _FENCE.match(line)
        if fence is not None:
            closer = fence_match.group(2) if fence_match else ""
            # A closing fence is the same character and at least as long, so a
            # three-backtick fence inside a wrapping four-backtick one does not
            # end it -- which is how a skill file shows a fenced example.
            if closer and closer[0] == fence[0] and len(closer) >= len(fence):
                fence = None
                para_open = False
            continue
        if fence_match:
            fence = fence_match.group(2)
            para_open = False
            continue

        if not line.strip():
            para_open = False
            continue

        indent = _indent_of(line)

        if _HEADING.match(line):
            # A heading ends the list outright rather than merely outdenting it:
            # a section is what an item with prose under it is supposed to
            # become, so a heading is where a list stops, not something in one.
            stack.clear()
            para_open = False
            continue

        if _BLOCKQUOTE.match(line) or _THEMATIC_BREAK.match(line) or _HTML.match(line):
            para_open = False
            reports += _inside_an_item(stack, path, line_no, indent, line)
            continue

        marker = _MARKER.match(line)
        if marker:
            while stack and stack[-1] > indent:
                stack.pop()
            stack.append(_content_column(marker))
            para_open = True
            continue

        if para_open and stack and indent < stack[-1]:
            reports.append(Report(FOLD, str(path), line_no, indent, stack[-1], line))
            # CommonMark keeps the line in the deeper item, so the stack stands.
            continue

        reports += _inside_an_item(stack, path, line_no, indent, line)
        enclosing = stack[-1] if stack else 0
        # An indented code block starts only where no paragraph is open; where
        # one is, a deeply indented line goes on continuing it.
        para_open = para_open or indent < enclosing + _CODE_INDENT

    return reports


def tracked_markdown() -> list[Path]:
    """Every markdown file the repository tracks."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md", "*.markdown"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [Path(p) for p in out.split("\0") if p]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="markdown files to check (default: every one the repository tracks)",
    )
    args = parser.parse_args(argv)

    paths = args.paths or tracked_markdown()
    if not paths:
        print("md_fold: no markdown files to check", file=sys.stderr)
        return _EXIT_MALFORMED

    reports: list[Report] = []
    for path in paths:
        try:
            reports += scan(path)
        except UnicodeDecodeError as exc:
            print(f"md_fold: {path} is not UTF-8 text: {exc}", file=sys.stderr)
            return _EXIT_MALFORMED
        except OSError as exc:
            print(f"md_fold: cannot read {path}: {exc}", file=sys.stderr)
            return _EXIT_MALFORMED

    for report in reports:
        print(report)
    if reports:
        print(f"\nmd_fold: {len(reports)} misshapen line(s) in {len(paths)} file(s)")
        return _EXIT_FAULT
    print(f"md_fold: no misshapen lines in {len(paths)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
