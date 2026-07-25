"""Shared frontmatter primitives for the K-Boat deterministic tools.

K-Boat notes are frontmatter-only or frontmatter-plus-body Markdown (see the
`kboat-notes` skill). The three tool groups — `lifecycle`, `repos`, `pick` — all
read that frontmatter and minimally rewrite single top-level lines, so the
reader, the fence finder, the scoped line writer, and the YAML-safe scalar
renderer live here once rather than in three copies.

The reader is a focused scanner, not a full YAML parser: it pulls top-level
`key: value` scalars and top-level block lists (`key:` then indented `- item`
lines), and an inline `key: []` empty list. Inline non-empty flow lists
(`key: [a, b]`) parse as the raw string; a writer handed one back reads its items
with `parse_flow_list` rather than splicing the source in. Because the scanner
decodes less than it reads, `parse_entries` hands back every entry's verbatim
source alongside the value — a whole-note writer puts back what it is not
changing rather than re-rendering it out of a lossy read, which is the only way
a property it does not understand survives. A key the scanner can only half-read
— one whose value runs on into lines it cannot model — is reported as unmodelled
rather than as its readable half, and so is absent from `parse_frontmatter`: a
value the scanner cannot account for in full is not a value, and a consumer
acting on the half would act on something the note does not say. The writers
touch only one line inside the frontmatter block, so field order and the body
always survive.

Reading is the exact inverse of `yaml_scalar`'s quoting, so a scalar with an
embedded quote or backslash round-trips losslessly. (This unifies on the old
repos reader; the old lifecycle/pick readers did not unescape, which only ever
affected such pathological values.)
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

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


def _scan_value(text: str) -> tuple[str, int]:
    """`text` up to any comment, and how many `[`/`{` it leaves open.

    Quote-aware in one pass, because neither question can be answered by counting
    characters: a `#` inside quotes is not a comment (nor is one without
    whitespace before it, as in `https://x#frag`), and a bracket inside quotes is
    not structure. Counting raw characters instead lets an ordinary title —
    `"Advent of Code [Day #3]"` — read as an unclosed collection and swallow
    every line after it.
    """
    depth, quote, i = 0, "", 0
    # A quote only opens a quoted scalar where a scalar can begin: the start of
    # the value, or just inside/after a flow separator. Elsewhere it is an
    # ordinary character — the apostrophe in `[Moore's law, ai]` must not open a
    # quote and hide the closing bracket behind it.
    fresh = True
    while i < len(text):
        char = text[i]
        if quote:
            if char == "\\" and quote == '"':
                i += 2
                continue
            if char == quote:
                quote = ""
        elif char == "#" and (i == 0 or text[i - 1].isspace()):
            return text[:i], depth
        elif char in "\"'" and fresh:
            quote, fresh = char, False
        elif char in "[{":
            depth, fresh = depth + 1, True
        elif char in "]}":
            depth, fresh = depth - 1, False
        elif char == ",":
            fresh = True
        elif not char.isspace():
            fresh = False
        i += 1
    return text, depth


def _continues(line: str, *, list_items: bool) -> bool:
    """Whether `line` belongs to the key above it rather than starting its own.

    An indented line always continues — a nested mapping, a block scalar, an
    indented list item. A `- item` at column zero continues only where a list is
    possible at all, which is under a key carrying no inline value: YAML lets a
    block list sit unindented there. After a key that already has a value the
    same line is not a list, just a line with no owner.
    """
    return line.startswith((" ", "\t")) or (list_items and line.startswith("- "))


@dataclass(frozen=True)
class Entry:
    """One top-level frontmatter entry, as source text and as a read value.

    `lines` is always the verbatim source, so a writer can put an entry back
    exactly as the human wrote it instead of re-rendering it from `value`.
    `modelled` says whether `value` actually represents those lines: it is False
    for a key the reader's grammar rejects (hyphenated, quoted), for a key whose
    indented block is a nested mapping or a block scalar, and for a line that is
    no key at all — `key` is then the key the line names, or None when there is
    none to name.
    """

    lines: tuple[str, ...]
    key: str | None = None
    value: Value = None
    modelled: bool = False


def continues_previous(line: str) -> bool:
    """Whether `line` would attach to whichever key precedes it.

    A line like this has no key of its own, so it takes its meaning entirely
    from its neighbour — which is what a writer that reorders entries has to
    know, or it hands the line to a key that never owned it.
    """
    return _continues(line, list_items=True)


def names_key(line: str, key: str) -> bool:
    """Whether `line` appears to set `key`, whether or not the reader can decode it.

    `_KEY_RE` decides what can be *decoded*; this is the looser question of what
    a line is about. A fail-closed check needs the looser one, because `"url": x`
    and `url : x` are undecodable precisely by being outside that grammar — and
    those are the notes it most matters not to overwrite.
    """
    head, sep, _ = line.partition(":")
    return bool(sep) and head.strip().strip("\"'") == key


def parse_entries(text: str) -> list[Entry]:
    """Read the leading frontmatter as an ordered list of entries.

    The scanner's full result, and the basis of every other reader here. A
    whitespace-only line *between* entries is dropped — it carries no value, and
    keeping it would let a re-written note accrete one blank line per write —
    but one inside a block is content (a paragraph break in a block scalar) and
    stays with it.
    """
    source = text.splitlines()
    start, close = _fence_bounds(source)
    lines = source[start:close]
    entries: list[Entry] = []
    i, count = 0, len(lines)
    while i < count:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("#"):
            # A comment owns nothing: it has no value for a block to belong to.
            # Letting it gather the lines below would hand a `- item` to a
            # comment, and a writer moving that entry would carry the item off
            # with it — into whichever key it landed under.
            entries.append(Entry((line,)))
            i += 1
            continue
        match = _KEY_RE.match(line)
        # A line the key grammar rejects still owns whatever follows it. Reading
        # it alone would leave its block as orphan lines, and an orphan line is
        # free to be moved — so a writer would hoist a hyphenated key's items
        # away from the key and leave YAML nothing can parse. Undecodable is not
        # the same as unattached.
        key, rest = (match.group(1), match.group(2)) if match else (None, line.partition(":")[2])
        # A trailing comment is not a value, so it decides nothing here: whether
        # the key can own an unindented `- item` list, whether it is empty, and
        # what it decodes to all come from `value`, not the raw line.
        value, depth = _scan_value(rest)
        bare = owns_list = value.strip() == ""
        # A value that *opens* with an inline collection runs on until it closes.
        # Requiring it to open with one, and stopping at the next column-zero key
        # either way, bounds what a miscount can swallow to the current entry.
        flowing = value.lstrip()[:1] in ("[", "{") and depth > 0
        block: list[str] = []
        comments: list[Entry] = []
        # Blank and comment lines are undecided until something after them
        # continues the block: only then are they inside it. Left buffered at the
        # end they belong to nobody, and `i` rewinds so the outer loop sees them.
        pending: list[str] = []
        j = i + 1
        while j < count:
            follower = lines[j]
            if not follower.strip() or (follower.startswith("#") and not flowing):
                pending.append(follower)
                j += 1
                continue
            if flowing and not _KEY_RE.match(follower):
                depth += _scan_value(follower)[1]
                flowing = depth > 0
            elif not _continues(follower, list_items=owns_list):
                break
            # A comment holds no value. Inside the entry it would make the key
            # unreadable and vanish whenever that key was rewritten, so it stands
            # on its own — without ending the block it sits in the middle of.
            comments.extend(Entry((p,)) for p in pending if p.strip())
            block.extend(p for p in pending if not p.strip())
            block.append(follower)
            pending.clear()
            j += 1
        i = j - len(pending)
        if key is None:
            entries.append(Entry((line, *block)))
            entries.extend(comments)
            continue
        if block:
            listed = [b for b in block if b.strip()]
            if bare and all(b.lstrip().startswith("- ") for b in listed):
                items = [_unquote(b.lstrip()[2:]) for b in listed]
                entries.append(Entry((line, *block), key, items, modelled=True))
            else:
                entries.append(Entry((line, *block), key))
        else:
            if bare:
                read: Value = None
            elif value.strip() == "[]":
                read = []
            else:
                read = _coerce(value)
            entries.append(Entry((line,), key, read, modelled=True))
        entries.extend(comments)
    return entries


def parse_frontmatter(text: str) -> dict[str, Value]:
    """Parse the leading frontmatter into scalars and top-level block lists.

    A top-level `key: value` becomes a scalar (`true`/`false` → bool, empty →
    None, quotes stripped). A top-level `key:` followed by indented `- item`
    lines becomes a `list[str]`; an inline `key: []` becomes an empty list. The
    note body (after the closing fence) is ignored, and so is anything the
    scanner cannot represent — `parse_entries` returns that instead.
    """
    return {e.key: e.value for e in parse_entries(text) if e.modelled and e.key is not None}


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


def yaml_list(items: Sequence[object] | None) -> str:
    if not items:
        return "[]"
    rendered = [_quote(s) if _needs_quote_flow(s) else s for s in (str(x) for x in items)]
    return "[" + ", ".join(rendered) + "]"


def parse_flow_list(text: str) -> list[str] | None:
    """The items of an inline `[a, b]` sequence, or None when `text` is not one.

    The inverse of `yaml_list`, for a writer handed an inline list as the raw
    source the reader returns for one. Reading it back into items is what lets
    the value be re-emitted through `yaml_list`, which is the only thing that
    knows how to quote a flow item — a string spliced in as-is carries whatever
    syntax it holds into the frontmatter, and one such value costs the whole
    block rather than its own field.

    Splitting is quote-aware, since a quoted item may hold the separator. None
    where the text is not a sequence this can read whole — an unclosed quote, a
    nested collection — so the caller has a value to quote rather than a shape
    invented for it.
    """
    stripped = text.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")) or len(stripped) < 2:
        return None
    inner = stripped[1:-1]
    if not inner.strip():
        return []
    items: list[str] = []
    current: list[str] = []
    quote = ""
    escaped = False
    for ch in inner:
        if quote:
            current.append(ch)
            if escaped:
                escaped = False
            elif quote == '"' and ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            current.append(ch)
        elif ch in "[]{}":
            return None  # a nested collection; splitting on commas would mangle it
        elif ch == ",":
            items.append("".join(current))
            current = []
        else:
            current.append(ch)
    if quote:
        return None
    items.append("".join(current))
    return [_unquote(item) for item in items]
