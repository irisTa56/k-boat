"""Fail where a markdown file puts prose inside a list item.

A paragraph and a list are exclusive: a list item is the line its marker is on,
and an item that needs prose under it is a section with a heading instead. So
the whole check is one question, asked of every paragraph in the document --
is it inside a list item, past that item's own marker line?

    - outer item

      a second paragraph belonging to that item

`rumdl` cannot ask it. Every line above is well-formed markdown line by line,
and what is wrong about it is only which block it belongs to. Hence a check of
its own.

Which block a line belongs to is CommonMark's question, and this script asks
CommonMark. Answering it by hand needs a classifier that knows when a marker
may interrupt a paragraph, where an indented code block starts and how far an
HTML comment runs -- and every disagreement with the real answer is a false
positive blocking a correct commit, which a commit gate can afford far less
than a miss. `markdown-it-py` answers instead: a faithful port of markdown-it,
100% CommonMark, and the successor the deprecated `commonmark.py` names.
Tables are enabled on top of it, because GitHub renders GFM and `rumdl` lints
GFM, and a table is the one thing GFM adds that changes block structure.

A lazily continued line needs no rule of its own here. CommonMark's
[lazy continuation](https://spec.commonmark.org/0.31.2/#lazy-continuation-line)
carries a paragraph across a line indented less than the block it began in:

    - outer item
      - deeper child
      text meant to continue the outer item

so the last line renders inside the deeper child rather than the outer item the
author indented it for. That is a paragraph line inside a list item past the
item's marker line, which is the fault above; the parser places the line and
this script reports it, without having to know that laziness is why. The
message does not draw the distinction either, because the two readings do not
differ in what the author has to do. A blank line before the line un-folds it
and leaves it prose inside the outer item, still failing this check, so the fix
either way is to fold the line onto the marker's or give the item a heading.

Only a paragraph is ever reported. A code block, a table, an HTML block and a
thematic break cannot live on a marker's line, so the remedy the rule names is
not open to them -- which also settles a code block's verdict by what it is
rather than by whether the author fenced or indented it. Inside a blockquote
nothing is scanned at all: the rule is about this repository's own prose, not
about text it quotes.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token

# CommonMark, plus the one GFM addition that can move a line into a different
# block. Every other GFM extension is inline-level, so none of them changes the
# answer this script reads off the token stream.
_PARSER = MarkdownIt("commonmark").enable("table")

_FRONTMATTER = "---"

# Exit 2, "the input isn't what this script expects", never exit 1, "a fault is
# there" -- the same split `coverage_floor.py` draws.
_EXIT_FAULT = 1
_EXIT_MALFORMED = 2

_REMEDY = (
    "is prose inside a list item -- a list item is the line its marker is on, "
    "so fold this onto that line or give the item a heading of its own"
)


@dataclass(frozen=True)
class Report:
    """One reported line: where it is, and the text sitting there."""

    path: str
    line_no: int
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line_no}: line {_REMEDY}\n    {self.text.strip()}"


def _without_frontmatter(lines: list[str]) -> str:
    """The document with any YAML frontmatter blanked out, line numbering kept.

    A skill file opens with frontmatter, and a block scalar's value there can
    be shaped exactly like a list with a line folded into it. Blanking rather
    than dropping the lines keeps every reported line number the file's own.
    """
    if lines and lines[0].strip() == _FRONTMATTER:
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == _FRONTMATTER:
                return "\n" * (i + 1) + "\n".join(lines[i + 1 :])
    return "\n".join(lines)


def _paragraph_lines(token: Token, item_start: int) -> range:
    """The 1-based lines of one paragraph that this rule can report.

    `map` is a 0-based half-open line range, so adding one to each end makes it
    1-based and inclusive. A paragraph beginning on the item's own first line
    begins on the marker line, which is the item rather than prose inside it,
    so that one line is dropped and the rest of the paragraph -- lazily
    continued or not -- stands.
    """
    if token.map is None:  # pragma: no cover - the parser maps every block
        return range(0)
    first, past_last = token.map
    return range(first + 2 if first == item_start else first + 1, past_last + 1)


def _prose_in_list_items(text: str) -> list[int]:
    """Every 1-based line the parser puts in a paragraph inside a list item."""
    reported: set[int] = set()
    # The first line of each open item, innermost last. Comparing a paragraph's
    # own first line against it is what tells the marker line from prose under
    # it, and it is the parser's number for both, never a column re-measured
    # off the source.
    item_starts: list[int] = []
    quoted = 0

    for token in _PARSER.parse(text):
        if token.type == "blockquote_open":
            quoted += 1
        elif token.type == "blockquote_close":
            quoted -= 1
        elif token.type == "list_item_open":
            item_starts.append(token.map[0] if token.map else -1)
        elif token.type == "list_item_close":
            item_starts.pop()
        elif token.type == "paragraph_open" and item_starts and not quoted:
            reported.update(_paragraph_lines(token, item_starts[-1]))

    return sorted(reported)


def scan(path: Path) -> list[Report]:
    """Return every misshapen line in one markdown file."""
    lines = path.read_text(encoding="utf-8").split("\n")
    return [
        Report(str(path), line_no, lines[line_no - 1])
        for line_no in _prose_in_list_items(_without_frontmatter(lines))
    ]


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
        print("md_shape: no markdown files to check", file=sys.stderr)
        return _EXIT_MALFORMED

    reports: list[Report] = []
    for path in paths:
        try:
            reports += scan(path)
        except UnicodeDecodeError as exc:
            print(f"md_shape: {path} is not UTF-8 text: {exc}", file=sys.stderr)
            return _EXIT_MALFORMED
        except OSError as exc:
            print(f"md_shape: cannot read {path}: {exc}", file=sys.stderr)
            return _EXIT_MALFORMED

    for report in reports:
        print(report)
    if reports:
        print(f"\nmd_shape: {len(reports)} misshapen line(s) in {len(paths)} file(s)")
        return _EXIT_FAULT
    print(f"md_shape: no misshapen lines in {len(paths)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
