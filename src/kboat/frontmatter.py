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


_DQ_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def _decode_double(inner: str) -> str:
    """Reverse `_quote`'s double-quoted escaping (`\\\\`, `\\"`, `\\n`, `\\t`,
    `\\r`) in one left-to-right pass; an unrecognised `\\x` keeps its backslash."""
    out: list[str] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == "\\" and i + 1 < len(inner):
            out.append(_DQ_ESCAPES.get(inner[i + 1], "\\" + inner[i + 1]))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _unquote(value: str) -> str:
    """Strip surrounding quotes and reverse the quote style's escaping — the
    inverse of `yaml_scalar`'s double-quoting, so a value with a quote, backslash,
    or control char round-trips. Single-quoted values (`''` -> `'`) are handled
    too, though `yaml_scalar` only ever emits double-quoted.
    """
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        return _decode_double(inner) if value[0] == '"' else inner.replace("''", "'")
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


def strip_frontmatter(text: str) -> str:
    """Return the note body, or `text` unchanged if it has no frontmatter block.

    The lenient counterpart to `body_after_frontmatter`: text that does not open
    with a `---` fence, or whose fence is never closed, is not frontmatter and is
    returned whole. Used where a leading block is optional (e.g. Daily notes)."""
    try:
        return body_after_frontmatter(text)
    except FrontmatterError:
        return text


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
# Indicators a *plain* scalar may not START with — YAML would read them as a
# block/flow/anchor/tag/directive marker. Mid-string most are harmless, so they
# are only checked at position 0 (see `_needs_quote`).
_LEADING_INDICATORS = "-?:,[]{}#&*!|>@%`\"'"
# In a *flow* context (an inline `[a, b]` list item) these are special anywhere,
# not just at the start, so a list item carrying one must be quoted.
_FLOW_SPECIAL = ":,?#&*!|>'\"%@`[]{}\t\n\r"
_CONTROL = "\t\n\r"


def _quote(text: str) -> str:
    # Escape backslash first, then the quote and the control chars a double-quoted
    # YAML scalar represents with a backslash escape — a raw newline/tab inside the
    # quotes would otherwise break the line. `_unquote` reverses these.
    body = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return '"' + body + '"'


def _needs_quote(text: str) -> bool:
    """Whether a top-level *plain* scalar needs quoting.

    Minimal: a `:`, `#`, or flow char *mid-string* is safe in block context
    (`https://…`, `a, b`), so quoting is forced only by edge whitespace, a YAML
    keyword/number, a leading indicator, an ambiguous colon (`: ` or a trailing
    one), a comment (` #`), or a control char.
    """
    if text == "" or text.strip() != text:
        return True
    if text.lower() in _YAML_KEYWORDS or _YAML_NUMBER_RE.match(text):
        return True
    if text[0] in _LEADING_INDICATORS:
        return True
    if ": " in text or " #" in text or text.endswith(":"):
        return True
    return any(c in _CONTROL for c in text)


def _needs_quote_flow(text: str) -> bool:
    """Stricter rule for an inline-list item, where `,[]{}:?#…` are special
    anywhere — over-quoting a rare odd item is fine; a misparse is not."""
    if text == "" or text.strip() != text:
        return True
    if text.lower() in _YAML_KEYWORDS or _YAML_NUMBER_RE.match(text):
        return True
    if text[0] == "-":
        return True
    return any(c in text for c in _FLOW_SPECIAL)


def yaml_scalar(value: object) -> str:
    """Render a top-level scalar, quoting only when bare YAML would misparse it
    (a keyword/number, a leading indicator, an ambiguous colon, …)."""
    if value is None:
        return ""
    text = str(value)
    return _quote(text) if _needs_quote(text) else text


def yaml_list(items: list[object] | None) -> str:
    if not items:
        return "[]"
    rendered = [_quote(s) if _needs_quote_flow(s) else s for s in (str(x) for x in items)]
    return "[" + ", ".join(rendered) + "]"
