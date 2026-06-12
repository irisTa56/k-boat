"""Shared frontmatter primitives for the K-Boat deterministic tools.

K-Boat notes are frontmatter-only or frontmatter-plus-body Markdown (see the
`kboat-notes` skill). The three tool groups — `lifecycle`, `repos`, `pick` — all
read that frontmatter and minimally rewrite single top-level lines, so the
reader, the fence finder, the scoped line writer, and the YAML-safe scalar
renderer live here once rather than in three copies.

The reader is a focused scanner, not a full YAML parser: it pulls top-level
`key: value` scalars and top-level block lists (`key:` then indented `- item`
lines), and an inline `key: []` empty list. Inline non-empty flow lists
(`key: [a, b]`) parse as the raw string — no current consumer reads one back as a
list (repos overwrites them, sources use block lists). The writers touch only one
line inside the frontmatter block, so field order and the body always survive.

Reading is the exact inverse of `yaml_scalar`'s quoting, so a scalar with an
embedded quote or backslash round-trips losslessly. (This unifies on the old
repos reader; the old lifecycle/pick readers did not unescape, which only ever
affected such pathological values.)
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$")

Scalar = str | bool | None
Value = Scalar | list[str]


class FrontmatterError(ValueError):
    """The note has no parseable `---` frontmatter block, or lacks a required field."""


# --------- reading ---------


def _unquote(value: str) -> str:
    """Strip surrounding quotes and reverse the quote style's escaping.

    The inverse of `yaml_scalar`'s double-quoting (`\\` -> `\\\\`, `"` -> `\\"`),
    so a value containing a quote or backslash round-trips. Single-quoted values
    (which YAML escapes as `''` -> `'`) are handled too, though `yaml_scalar`
    only ever emits double-quoted.
    """
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        if value[0] == '"':
            return inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner.replace("''", "'")
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


def body_after_frontmatter(text: str) -> str:
    """Return the note body (everything after the closing `---` fence)."""
    lines = text.splitlines(keepends=True)
    _, end = _fence_bounds(lines)
    return "".join(lines[end + 1 :])


# --------- writing ---------


def set_field(
    text: str,
    key: str,
    value: str | None,
    *,
    insert_if_absent: bool = False,
    insert_after: str | None = None,
) -> str:
    """Rewrite the single top-level `key:` line inside the frontmatter block.

    `value` is the already-rendered text after `key: ` (e.g. `"true"`, a date), or
    None to leave the field bare (`key:`). If the key is absent: when
    `insert_if_absent`, insert a new line after the top-level `insert_after` key
    (or before the closing fence if that key is missing too); otherwise raise
    `FrontmatterError`. Only that one line, inside the frontmatter, changes.
    """
    rendered = f"{key}:" if value is None else f"{key}: {value}"
    lines = text.splitlines(keepends=True)
    start, end = _fence_bounds(lines)
    anchor: int | None = None
    for i in range(start, end):
        body, newline = _split_newline(lines[i])
        if body[:1].isspace():
            continue
        if body.startswith(f"{key}:"):
            lines[i] = rendered + newline
            return "".join(lines)
        if insert_after is not None and body.startswith(f"{insert_after}:"):
            anchor = i
    if not insert_if_absent:
        raise FrontmatterError(f"note has no top-level '{key}' line to rewrite")
    at = anchor if anchor is not None else end - 1
    _, newline = _split_newline(lines[at]) if start < end else ("", "\n")
    lines.insert(at + 1, rendered + (newline or "\n"))
    return "".join(lines)


def set_fields(text: str, rendered: Mapping[str, str | None]) -> str:
    """Rewrite several existing top-level lines in one pass.

    Each key in `rendered` must already exist as a top-level line (callers target
    always-present fields); any missing key is a `FrontmatterError` listing all of
    them, rather than a silent insert. Values are already-rendered text. Field
    order and the body are preserved.
    """
    lines = text.splitlines(keepends=True)
    start, end = _fence_bounds(lines)
    remaining = dict(rendered)
    for i in range(start, end):
        body, newline = _split_newline(lines[i])
        if body[:1].isspace() or body.lstrip().startswith("- "):
            continue
        match = _KEY_RE.match(body)
        if not match:
            continue
        key = match.group(1)
        if key in remaining:
            value = remaining.pop(key)
            lines[i] = (f"{key}:" if value is None else f"{key}: {value}") + newline
    if remaining:
        raise FrontmatterError(
            "note has no top-level line(s) to rewrite: " + ", ".join(sorted(remaining))
        )
    return "".join(lines)


# --------- YAML-safe scalar rendering ---------


# Whole-string values YAML reads as something other than a string.
_YAML_KEYWORDS = frozenset({"true", "false", "null", "yes", "no", "on", "off", "none", "~"})
# A value YAML would parse as a number (int, float, or scientific notation).
_YAML_NUMBER_RE = re.compile(r"^[+-]?(\d[\d_]*\.?[\d_]*|\.\d[\d_]*)([eE][+-]?\d+)?$")
# Characters that need quoting anywhere — block indicators, the flow indicators
# (`,[]{}`) that matter because `yaml_scalar` also renders inline list items
# (`topics: [a, b]`), and the whitespace controls (tab/newline/CR) that a bare
# scalar cannot carry (an interior tab makes real YAML parsers reject the line).
_DANGEROUS_CHARS = ":#&*!|>'\"%@`,[]{}\t\n\r"


def _needs_quote(text: str) -> bool:
    if text == "" or text.strip() != text:
        return True
    if text.lower() in _YAML_KEYWORDS:  # `true`, `null`, ... would become bool/null
        return True
    if _YAML_NUMBER_RE.match(text):  # a digit-only/numeric value would become a number
        return True
    if text[0] in "-?":  # a leading indicator (`- foo` is a list, `? foo` a key)
        return True
    return any(c in text for c in _DANGEROUS_CHARS)


def yaml_scalar(value: object) -> str:
    """Render a scalar, quoting it whenever bare YAML would misparse it.

    Covers dangerous characters, leading indicators, and whole-string values that
    YAML reads as a bool/null/number (`true`, `123`, `- x`) — important for free
    GitHub text in `description`.
    """
    if value is None:
        return ""
    text = str(value)
    if _needs_quote(text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def yaml_list(items: list[object] | None) -> str:
    if not items:
        return "[]"
    return "[" + ", ".join(yaml_scalar(x) for x in items) + "]"
