"""Read and minimally edit source-note frontmatter.

Source notes are frontmatter-only Markdown (see `kboat-notes`). We only need a
handful of top-level scalar fields to drive the lifecycle, so this is a focused
reader rather than a full YAML parser: it pulls scalar `key: value` lines and
ignores list values (e.g. `topics`) and any note body.

Writes are deliberately scoped — only the single top-level `filed_date:` line,
and only inside the frontmatter block — so the schema's field order and all other
formatting survive intact, and a stray `filed_date:` in a body (should one ever
exist) is never touched. The reader and writer share one fence finder so they
can never disagree about where the frontmatter is.
"""

from __future__ import annotations

import re

_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$")

Scalar = str | bool | None


class FrontmatterError(ValueError):
    """The note has no parseable `---` frontmatter block, or lacks a field the
    lifecycle requires."""


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _coerce(raw: str) -> Scalar:
    value = raw.strip()
    if value == "":
        return None
    if value in ("true", "false"):
        return value == "true"
    return _unquote(value)


def _fence_bounds(lines: list[str]) -> tuple[int, int]:
    """Return `(start, end)` such that `lines[start:end]` is the frontmatter
    content and `lines[0]` / `lines[end]` are the opening / closing `---` fences.

    Works on lines split with or without keepends — the fence test strips first.
    """
    if not lines or lines[0].strip() != "---":
        raise FrontmatterError("note does not start with a '---' frontmatter fence")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return 1, i
    raise FrontmatterError("frontmatter block is not closed by a '---' fence")


def _split_newline(line: str) -> tuple[str, str]:
    """Split a line into its content and its trailing newline (`""` if none)."""
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def parse_frontmatter(text: str) -> dict[str, Scalar]:
    """Parse the leading frontmatter block into scalar values.

    Top-level `key: value` lines become entries; list values collapse to None
    (their continuation `- ...` lines are skipped) since the lifecycle never
    reads them. A bare `key:` is None — an empty field.
    """
    lines = text.splitlines()
    start, end = _fence_bounds(lines)
    result: dict[str, Scalar] = {}
    for line in lines[start:end]:
        if line.startswith((" ", "\t")) or line.lstrip().startswith("- "):
            continue  # list item or nested value — not a top-level scalar
        match = _KEY_RE.match(line)
        if match:
            result[match.group(1)] = _coerce(match.group(2))
    return result


def set_filed_date(text: str, value: str | None) -> str:
    """Return `text` with its top-level `filed_date:` line rewritten.

    `value` is a `YYYY-MM-DD` string to stamp, or None to clear the field back
    to `filed_date:`. Only that one line, inside the frontmatter block, changes.
    """
    replacement = "filed_date:" if value is None else f"filed_date: {value}"
    lines = text.splitlines(keepends=True)
    start, end = _fence_bounds(lines)
    for i in range(start, end):
        body, newline = _split_newline(lines[i])
        # Mirror the reader: a top-level key, not indented and not a list item.
        if not body[:1].isspace() and body.startswith("filed_date:"):
            lines[i] = replacement + newline
            return "".join(lines)
    raise FrontmatterError("note has no top-level 'filed_date' line to rewrite")
