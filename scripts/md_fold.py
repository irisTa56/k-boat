"""Fail if a markdown line is folded into a deeper list item than it sits in.

Stdlib-only by design, for the same reason `coverage_floor.py` is: it runs from
the `qa:md` gate and from CI's `markdown-lint` job, neither of which installs
anything to run it.

CommonMark's [lazy continuation](https://spec.commonmark.org/0.31.2/#lazy-continuation-line)
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

`rumdl` does not catch this: every line here is well-formed markdown, and what
is wrong is only which block it ends up in. Hence a check of its own.

The scan is deliberately narrow. It reports one thing -- a paragraph line whose
indentation is shallower than the innermost open list item's content column,
with no blank line between -- and nothing else. A markdown file that renders as
its indentation reads passes, whatever else may be said about it.

That one thing includes a line sitting at an item's own marker column rather
than under a deeper child: short of the content column, it too would land
outside the list if a blank line closed the paragraph, and laziness keeps it in
the item. The remedy is the same either way.

Where the check cannot decide, it does not report. It gates commits, so a false
positive costs more than a miss, and the block starts that interrupt a paragraph
are treated generously for that reason.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# A list marker plus the whitespace after it. The match end is the item's
# content column, which is what decides whether a later line is inside it.
_MARKER = re.compile(r"^(\s*)(?:[-*+]|\d{1,9}[.)])(\s+)")
_FENCE = re.compile(r"^(\s*)(```+|~~~+)")
_HEADING = re.compile(r"^(\s*)#{1,6}(\s|$)")
# The other block starts that interrupt an open paragraph rather than continuing
# it, so a line beginning with one is never folded whatever its indentation.
# Scanning inside a blockquote is out of scope -- this repo has none under a list
# item, and reporting one wrongly would block a correct commit.
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
# paragraph, so either way nothing is folded past this line.
_THEMATIC_BREAK = re.compile(r"^\s*(?:(?:\*\s*){3,}|(?:_\s*){3,}|(?:-\s*){3,})$")
_FRONTMATTER = "---"

# Exit 2, "the input isn't what this script expects", never exit 1, "a fold is
# there" -- the same split `coverage_floor.py` draws.
_EXIT_FOLD = 1
_EXIT_MALFORMED = 2


class Fold:
    """One folded line: where it is, and which item swallowed it."""

    def __init__(self, path: str, line_no: int, indent: int, swallowed_by: int, text: str):
        self.path = path
        self.line_no = line_no
        self.indent = indent
        self.swallowed_by = swallowed_by
        self.text = text

    def __str__(self) -> str:
        return (
            f"{self.path}:{self.line_no}: line at column {self.indent} is folded into "
            f"the item at column {self.swallowed_by} -- put a blank line before it "
            f"or indent it to that item\n    {self.text.strip()}"
        )


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _content_column(marker: re.Match[str]) -> int:
    """The column an item's content starts at, capped as CommonMark caps it."""
    spaces = len(marker.group(2))
    if spaces > _MAX_MARKER_SPACES:
        return marker.end() - spaces + 1
    return marker.end()


def scan(path: Path) -> list[Fold]:
    """Return every folded line in one markdown file."""
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    folds: list[Fold] = []

    # Content columns of the list items currently open, shallowest first.
    stack: list[int] = []
    # Whether a paragraph is open, which is what lazy continuation needs.
    para_open = False
    fence: str | None = None

    start = 0
    # A skill file opens with YAML frontmatter, whose `-` lines would otherwise
    # read as list markers.
    if lines and lines[0].strip() == _FRONTMATTER:
        for i, line in enumerate(lines[1:], 2):
            if line.strip() == _FRONTMATTER:
                start = i
                break

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

        if (
            _HEADING.match(line)
            or _BLOCKQUOTE.match(line)
            or _THEMATIC_BREAK.match(line)
            or _HTML.match(line)
        ):
            # Each of these interrupts a paragraph, so none is folded into one.
            para_open = False
            while stack and stack[-1] > indent:
                stack.pop()
            continue

        marker = _MARKER.match(line)
        if marker:
            while stack and stack[-1] > indent:
                stack.pop()
            stack.append(_content_column(marker))
            para_open = True
            continue

        if para_open and stack and indent < stack[-1]:
            folds.append(Fold(str(path), line_no, indent, stack[-1], line))
            # CommonMark keeps the line in the deeper item, so the stack stands.
            continue

        while stack and stack[-1] > indent:
            stack.pop()
        enclosing = stack[-1] if stack else 0
        # An indented code block starts only where no paragraph is open; where
        # one is, a deeply indented line goes on continuing it.
        para_open = para_open or indent < enclosing + _CODE_INDENT

    return folds


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

    folds: list[Fold] = []
    for path in paths:
        try:
            folds += scan(path)
        except UnicodeDecodeError as exc:
            print(f"md_fold: {path} is not UTF-8 text: {exc}", file=sys.stderr)
            return _EXIT_MALFORMED
        except OSError as exc:
            print(f"md_fold: cannot read {path}: {exc}", file=sys.stderr)
            return _EXIT_MALFORMED

    for fold in folds:
        print(fold)
    if folds:
        print(f"\nmd_fold: {len(folds)} folded line(s) in {len(paths)} file(s)")
        return _EXIT_FOLD
    print(f"md_fold: no folded lines in {len(paths)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
