"""Read source-note frontmatter and minimally rewrite the `picked:` line.

Source notes are frontmatter-only Markdown (see `kboat-notes`). The daily pick
needs a few scalar fields plus the `topics` list, so this reader pulls top-level
`key: value` scalars and top-level block lists (`key:` followed by indented
`- item` lines); it ignores the note body.

The writer is deliberately scoped: it rewrites only the single top-level
`picked:` line inside the frontmatter block (inserting one after `blocked:` if a
note predates the field), so the schema's field order and all other formatting
survive intact. The reader and writer share one fence finder so they can never
disagree about where the frontmatter is.
"""

from __future__ import annotations

import re

_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$")

Scalar = str | bool | None
Value = Scalar | list[str]


class FrontmatterError(ValueError):
    """The note has no parseable `---` frontmatter block."""


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
    """Return `(start, end)` so `lines[start:end]` is the frontmatter content and
    `lines[0]` / `lines[end]` are the opening / closing `---` fences."""
    if not lines or lines[0].strip() != "---":
        raise FrontmatterError("note does not start with a '---' frontmatter fence")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return 1, i
    raise FrontmatterError("frontmatter block is not closed by a '---' fence")


def _split_newline(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def parse_frontmatter(text: str) -> dict[str, Value]:
    """Parse the leading frontmatter into scalars and top-level block lists.

    A top-level `key: value` becomes a scalar (`true`/`false` → bool, empty →
    None, quotes stripped). A top-level `key:` followed by indented `- item`
    lines becomes a `list[str]`; an inline `key: []` becomes an empty list. The
    note body (after the closing fence) is ignored.
    """
    lines = text.splitlines()
    start, end = _fence_bounds(lines)
    result: dict[str, Value] = {}
    i = start
    while i < end:
        line = lines[i]
        if line.startswith((" ", "\t")) or line.lstrip().startswith("- "):
            i += 1
            continue
        match = _KEY_RE.match(line)
        if not match:
            i += 1
            continue
        key, raw = match.group(1), match.group(2)
        if raw.strip() == "":
            # Possibly a block list: collect following indented `- item` lines.
            items: list[str] = []
            j = i + 1
            while j < end and lines[j].lstrip().startswith("- "):
                items.append(_unquote(lines[j].lstrip()[2:]))
                j += 1
            if items:
                result[key] = items
                i = j
                continue
            result[key] = None
        elif raw.strip() == "[]":
            result[key] = []
        else:
            result[key] = _coerce(raw)
        i += 1
    return result


def set_picked(text: str, value: bool) -> str:
    """Return `text` with its top-level `picked:` line set to `value`.

    Rewrites the existing `picked:` line if present; otherwise inserts one right
    after the top-level `blocked:` line (its schema neighbour), or, failing that,
    just before the closing fence. Only the frontmatter block is touched.
    """
    rendered = f"picked: {'true' if value else 'false'}"
    lines = text.splitlines(keepends=True)
    start, end = _fence_bounds(lines)
    blocked_at: int | None = None
    for i in range(start, end):
        body, newline = _split_newline(lines[i])
        if body[:1].isspace():
            continue
        if body.startswith("picked:"):
            lines[i] = rendered + newline
            return "".join(lines)
        if body.startswith("blocked:"):
            blocked_at = i
    # No picked line — insert one, copying the newline style of a nearby line.
    anchor = blocked_at if blocked_at is not None else end - 1
    _, newline = _split_newline(lines[anchor]) if start < end else ("", "\n")
    lines.insert(anchor + 1, rendered + (newline or "\n"))
    return "".join(lines)
