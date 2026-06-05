"""Read, render, and minimally rewrite repo-note frontmatter.

Repo notes are frontmatter plus a single `## Notes` body section (see the
`kboat-notes` skill). Lists (`language`, `topics`, `domain`) are written inline
(flow style: `topics: [a, b, c]`), so every top-level field is one line — which
lets `refresh` rewrite a field by replacing its single line, leaving the field
order, the judgement layer, and the human-edited body untouched.

The reader is focused, not a full YAML parser: it pulls scalar `key: value`
lines (lists collapse to None — `refresh` overwrites them, never reads them) and
can hand back the body after the frontmatter.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$")

Scalar = str | bool | None

# Canonical frontmatter field order (open links -> read checkbox -> GitHub
# metadata -> classification -> status -> routine dates). Mirrors the schema
# table in `kboat-notes` "Repo note".
FIELD_ORDER = (
    "type",
    "title",
    "url",
    "homepage",
    "read",
    "description",
    "language",
    "topics",
    "stars",
    "archived",
    "created_at",
    "last_commit",
    "license",
    "role",
    "domain",
    "summary",
    "status",
    "added_date",
    "refreshed_date",
)


class FrontmatterError(ValueError):
    """The note has no parseable `---` frontmatter block, or lacks a required field."""


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


def parse_frontmatter(text: str) -> dict[str, Scalar]:
    """Parse the leading frontmatter block into scalar values (lists -> None)."""
    lines = text.splitlines()
    start, end = _fence_bounds(lines)
    result: dict[str, Scalar] = {}
    for line in lines[start:end]:
        if line.startswith((" ", "\t")) or line.lstrip().startswith("- "):
            continue
        match = _KEY_RE.match(line)
        if match:
            result[match.group(1)] = _coerce(match.group(2))
    return result


def body_after_frontmatter(text: str) -> str:
    """Return the note body (everything after the closing `---` fence)."""
    lines = text.splitlines(keepends=True)
    _, end = _fence_bounds(lines)
    return "".join(lines[end + 1 :])


# --------- rendering ---------


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

    Covers dangerous characters, leading indicators, and whole-string values
    that YAML reads as a bool/null/number (`true`, `123`, `- x`) — important for
    free GitHub text in `description`.
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


def render_value(key: str, value: object) -> str:
    """Render one frontmatter value to its inline YAML form, keyed by field."""
    if key in ("language", "topics", "domain"):
        items: list[object] = list(value) if isinstance(value, list) else []
        return yaml_list(items)
    if key in ("read", "archived"):
        return "true" if value else "false"
    if key == "stars":
        return str(value) if value is not None else "0"
    return yaml_scalar(value)


def build_repo_note(fields: Mapping[str, object], notes_body: str = "") -> str:
    """Assemble a full repo note: ordered frontmatter + a `## Notes` section.

    `notes_body` is the content under `## Notes` (may be empty). Unknown keys in
    `fields` are appended after the ordered ones so nothing is silently dropped.
    """
    keys = list(FIELD_ORDER) + [k for k in fields if k not in FIELD_ORDER]
    lines = ["---"]
    for key in keys:
        if key in fields:
            lines.append(f"{key}: {render_value(key, fields[key])}")
    lines.append("---")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    body = notes_body.strip("\n")
    if body:
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


def set_fields(text: str, updates: Mapping[str, object]) -> str:
    """Rewrite the named top-level frontmatter lines in place.

    Each key in `updates` must already exist as a top-level line in the
    frontmatter (refresh targets always-present GitHub-derived fields); a missing
    key is a `FrontmatterError` rather than a silent insert, so a malformed note
    is surfaced, not patched over. The field order, every other field, and the
    body are preserved.
    """
    lines = text.splitlines(keepends=True)
    start, end = _fence_bounds(lines)
    remaining = dict(updates)
    for i in range(start, end):
        body, newline = _split_newline(lines[i])
        if body[:1].isspace() or body.lstrip().startswith("- "):
            continue
        match = _KEY_RE.match(body)
        if not match:
            continue
        key = match.group(1)
        if key in remaining:
            lines[i] = f"{key}: {render_value(key, remaining.pop(key))}" + newline
    if remaining:
        raise FrontmatterError(
            "note has no top-level line(s) to rewrite: " + ", ".join(sorted(remaining))
        )
    return "".join(lines)
