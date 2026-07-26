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

Where a line ends is the one thing this scanner may not decide for itself: the
note is also read by Obsidian and by whatever else opens the vault, and a value
carrying a character only *this* reader breaks on would be a different value to
each of them — the tail of a page summary arriving as a property of the note.
So the split is YAML's own (`\\n`, `\\r`, `\\r\\n`), and every other character
that could pass for a break is escaped on the way out rather than written raw.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_PLAIN_KEY = r"[A-Za-z_][A-Za-z0-9_]*"
_KEY_RE = re.compile(rf"^({_PLAIN_KEY}):(.*)$")

Scalar = str | bool | None
Value = Scalar | list[str]


class FrontmatterError(ValueError):
    """The note has no parseable `---` frontmatter block, or lacks a required field."""


# --------- lines ---------


# Where a line ends, for YAML and for Markdown alike: `\n`, `\r`, `\r\n`, and
# nothing else. `str.splitlines` also breaks on the vertical tab, form feed, file
# and group and record separators, NEL, and the Unicode line and paragraph
# separators, which YAML 1.2 and Obsidian both read as ordinary characters
# inside a scalar — so a value carrying one would be read here as two lines, and
# the tail of somebody's summary would become a property of the note.
_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")


def _split_newline(line: str) -> tuple[str, str]:
    """A line from `_lines_keepends`, split from its own terminator.

    All three terminators, because a rewriter puts back what it took off: drop a
    lone `\\r` here and the rewritten line would run into the one below it.
    """
    for ending in ("\r\n", "\n", "\r"):
        if line.endswith(ending):
            return line[: -len(ending)], ending
    return line, ""


def _lines_keepends(text: str) -> list[str]:
    """`text` split into lines, each with its own terminator still on it."""
    out: list[str] = []
    start = 0
    for match in _LINE_BREAK_RE.finditer(text):
        out.append(text[start : match.end()])
        start = match.end()
    if start < len(text):
        out.append(text[start:])
    return out


def split_lines(text: str) -> list[str]:
    """`text` split into lines, without their terminators.

    Derived from `_lines_keepends` rather than restated, and shared with the note
    writer, so that where a line ends is one rule and not three that agree by
    inspection — two readers of this vault disagreeing about it is how a value
    ends up read as two things.
    """
    return [_split_newline(line)[0] for line in _lines_keepends(text)]


# --------- reading ---------


# YAML's double-quoted escapes, all of them: `yaml_scalar` emits only a few, but
# a note is also written by Obsidian and read back here, and an escape decoded as
# its own literal text is a value that changes every time it passes through.
_DQ_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    '"': '"',
    "\\": "\\",
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "v": "\v",
    "f": "\f",
    "e": "\x1b",
    "N": "\x85",
    "_": "\xa0",
    "L": "\u2028",
    "P": "\u2029",
    "/": "/",
    " ": " ",
}
# The numeric escapes, by how many hex digits each takes.
_DQ_HEX_WIDTHS = {"x": 2, "u": 4, "U": 8}


_HEX_RE = re.compile(r"[0-9A-Fa-f]+")


def _decode_numeric(digits: str, width: int) -> str | None:
    """The character `digits` names, or None if they name none.

    An escape shorter than its width names nothing — `\\x1` is not U+0001 — and
    neither does a lone surrogate: that is a code point no UTF-8 file can hold,
    so decoding one would leave a value the note cannot be written back with.
    """
    if len(digits) != width or not _HEX_RE.fullmatch(digits):
        return None
    code = int(digits, 16)
    if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
        return None
    return chr(code)


def _decode_double(inner: str) -> str:
    """Reverse double-quoted escaping — the named escapes and the numeric
    (`\\xNN`, `\\uNNNN`, `\\UNNNNNNNN`) ones — in one left-to-right pass; an
    escape that is neither keeps its backslash rather than being invented."""
    out: list[str] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == "\\" and i + 1 < len(inner):
            width = _DQ_HEX_WIDTHS.get(inner[i + 1], 0)
            decoded = _decode_numeric(inner[i + 2 : i + 2 + width], width) if width else None
            if decoded is not None:
                out.append(decoded)
                i += 2 + width
                continue
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
        # A comment opens after YAML's own whitespace — a space or a tab — and
        # nowhere else. `str.isspace()` is wider: it counts NBSP and the ideographic
        # space, both of which turn up in scraped page text right before a `#`, and
        # reading one as a comment would cut a title off at a character YAML keeps.
        elif char == "#" and (i == 0 or text[i - 1] in " \t"):
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
    source = split_lines(text)
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
    lines = _lines_keepends(text)
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
    lines = _lines_keepends(text)
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
    lines = _lines_keepends(text)
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
# not just at the start, so a list item carrying one must be quoted. The
# characters that may not stand for themselves at all are `is_unprintable`'s,
# asked separately so the two contexts cannot answer it differently.
_FLOW_SPECIAL = ":,?#&*!|>'\"%@`[]{}"
# The escapes `_quote` writes by name, for the characters that read best that way.
_NAMED_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def is_unprintable(char: str) -> bool:
    """Whether `char` may not stand for itself inside a quoted scalar.

    The C0 and C1 control ranges plus the Unicode line and paragraph separators
    — YAML's own non-printable set. What matters here is that they never reach
    the note raw: several of them end a line for one reader of this vault and
    not for another, so a value carrying one would be a different value
    depending on who opened the file.
    """
    code = ord(char)
    return code < 0x20 or 0x7F <= code <= 0x9F or code in (0x2028, 0x2029)


def _quote(text: str) -> str:
    # Escape the backslash and the quote, then every character that cannot stand
    # for itself — by name where YAML has one, numerically otherwise. `_unquote`
    # reverses all of it.
    body = []
    for char in text:
        if char in _NAMED_ESCAPES:
            body.append(_NAMED_ESCAPES[char])
        elif is_unprintable(char):
            code = ord(char)
            body.append(f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}")
        else:
            body.append(char)
    return '"' + "".join(body) + '"'


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
    return any(is_unprintable(c) for c in text)


def _needs_quote_flow(text: str) -> bool:
    """Stricter rule for an inline-list item, where `,[]{}:?#…` are special
    anywhere — over-quoting a rare odd item is fine; a misparse is not."""
    if text == "" or text.strip() != text:
        return True
    if text.lower() in _YAML_KEYWORDS or _YAML_NUMBER_RE.match(text):
        return True
    if text[0] == "-":
        return True
    return any(c in _FLOW_SPECIAL or is_unprintable(c) for c in text)


# A text every YAML reader reads back as the integer it spells: ASCII digits
# only, because YAML's own int resolver is (an Arabic-Indic digit passes
# `isdigit` and reads back as a string), and no leading zero, because YAML 1.1
# reads `010` as octal. A note is read by kboat's scanner, by Obsidian, and by
# whatever else opens the vault, so "an integer" has to mean one thing to all.
_YAML_INT_RE = re.compile(r"-?(0|[1-9][0-9]*)")


_PLAIN_KEY_RE = re.compile(_PLAIN_KEY)


def is_plain_key(key: str) -> bool:
    """Whether `key` can be written in front of a `:` as the property it is.

    The reader's own key grammar (`_KEY_RE`) with nothing after it, so what a
    writer emits by this rule is exactly what the reader takes back out. That is
    the point: a key holding a `:` or a newline would end the entry, or the
    whole block, in the middle of itself, and one merely outside the grammar
    would be written and then be unreadable — a property `kboat-validate` cannot
    see, on a note that reports itself as written.
    """
    return bool(_PLAIN_KEY_RE.fullmatch(key))


def is_yaml_int(text: str) -> bool:
    """Whether `text` is an integer to every YAML reader, and so safe unquoted.

    The one definition of "this field holds a number", shared by the writer
    (which emits it bare only here) and the validator (which reports `not_int`
    everywhere else). Two definitions would disagree somewhere, and the pair
    they disagree on is exactly a value written as valid and reported as not.
    """
    return bool(_YAML_INT_RE.fullmatch(text))


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
    # A null item is the empty item, as it is for `yaml_scalar` — `str(None)`
    # would write the word `None` into the note, a value nobody supplied.
    texts = ("" if x is None else str(x) for x in items)
    rendered = [_quote(s) if _needs_quote_flow(s) else s for s in texts]
    return "[" + ", ".join(rendered) + "]"


def parse_flow_list(text: str) -> list[str] | None:
    """The items of an inline `[a, b]` sequence, or None when `text` is not one.

    The inverse of `yaml_list`, for a writer handed an inline list as the raw
    source the reader returns for one. Reading it back into items is what lets
    the value be re-emitted through `yaml_list`, which is the only thing that
    knows how to quote a flow item — a string spliced in as-is carries whatever
    syntax it holds into the frontmatter, and one such value costs the whole
    block rather than its own field.

    Splitting is quote-aware, since a quoted item may hold the separator — and
    a quote counts as one only where an item can begin, the same rule
    `_scan_value` follows and for the same reason: the apostrophe in
    `[Moore's law, ai]` is a character, not the start of a quoted scalar.

    None where the text is not a sequence this can read whole — an unclosed
    quote, a nested collection, an item that is a mapping entry (`[a: b]`, which
    needs no braces to be one) — so the caller has a value to quote rather than
    a shape invented for it.
    """
    stripped = text.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return None
    inner = stripped[1:-1]
    if not inner.strip():
        return []
    items: list[str] = []
    current: list[str] = []
    quote = ""
    escaped = False
    fresh = True  # whether an item can begin here, so a quote would open one
    colon = False  # an unquoted `:`, whose next character decides what it is
    closed = False  # a quoted scalar just ended, so only a separator may follow
    i = 0
    while i < len(inner):
        ch = inner[i]
        i += 1
        if quote:
            # A single-quoted scalar escapes its own quote by doubling it, so a
            # `''` is a character and not the end of the item — the same rule
            # `_unquote` reverses, and the two have to agree or a comma inside
            # the item splits it.
            if quote == "'" and ch == "'" and inner[i : i + 1] == "'":
                current.append("''")
                i += 1
                continue
            current.append(ch)
            if escaped:
                escaped = False
            elif quote == '"' and ch == "\\":
                escaped = True
            elif ch == quote:
                quote, closed = "", True
            continue
        if colon and (ch.isspace() or ch == ","):
            # `a: b` is a mapping entry, which YAML writes inside a sequence
            # without braces — so the `[]{}` check below never sees it.
            return None
        if closed and not (ch.isspace() or ch == ","):
            # A quoted scalar is the whole of its item, so anything but a
            # separator after it says the item is something else: `"a":b` is
            # that mapping again, with no space to give it away.
            return None
        colon = ch == ":"
        if ch in "[]{}":
            return None  # a nested collection; splitting on commas would mangle it
        if ch == ",":
            items.append("".join(current))
            current, fresh, closed = [], True, False
            continue
        current.append(ch)
        if ch in "\"'" and fresh:
            quote, fresh = ch, False
        elif not ch.isspace():
            fresh = False
    if quote or colon:  # a trailing `:` is a mapping key with nothing after it
        return None
    items.append("".join(current))
    # A trailing comma closes the last item rather than opening another, so the
    # empty tail it leaves is not an item the note holds.
    if len(items) > 1 and not items[-1].strip():
        items.pop()
    return [_unquote(item) for item in items]
