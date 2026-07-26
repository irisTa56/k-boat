"""Schema-driven note assembly.

`build_note` renders a note's frontmatter in its schema's field order (block lists
multi-line, inline lists in flow style), plus an optional body appended verbatim.
`render_field` renders one value to its single-line YAML form by the field's kind
— the one place per-field rendering lives, shared by `build_note` and the repos
`set_fields` rewriter. The schema (`kboat.schema`) is the field order and kinds;
this module is how a note is written from them.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path

from kboat.frontmatter import (
    Entry,
    body_after_frontmatter,
    continues_previous,
    is_plain_key,
    is_unprintable,
    is_yaml_int,
    names_key,
    parse_entries,
    parse_flow_list,
    split_lines,
    yaml_list,
    yaml_scalar,
)
from kboat.io_utils import atomic_write_text
from kboat.schema import DIR_BY_TYPE, Field, Kind, NoteSchema


class BadInputError(Exception):
    """A record the writer refuses to act on, because it does not say a note.

    Distinct from a failed write: nothing about the vault would make this record
    writable, so the caller's move is to fix the record and send it again. The
    CLI boundary (`kboat.cli`) maps it to its own exit code for exactly that.
    """


def render_field(field: Field | None, value: object) -> str:
    """Render one value to its inline YAML text, by the field's kind.

    An unknown field (`None`) falls back to a plain scalar. This always returns a
    single line: a block list's items are laid out by `build_note`, and a block
    field reaches here only for a value with no items to lay out, which renders
    as the scalar it is.
    """
    if field is None:
        return yaml_scalar(value)
    if field.kind is Kind.BOOL:
        return "true" if value else "false"
    if field.kind is Kind.INT:
        default = field.default if field.default is not None else 0
        text = str(value if value is not None else default)
        # An int is written bare, which is only safe while it is one: a value
        # carrying `: ` or a newline would take the whole frontmatter block out
        # of YAML rather than cost its own field. Quoted, it stays a scalar and
        # reaches `kboat-validate` as the `not_int` it is — the same predicate
        # decides both, so the two cannot disagree about what a number is.
        return text if is_yaml_int(text) else yaml_scalar(text)
    if field.kind is Kind.STR_LIST:
        if isinstance(value, list):
            return yaml_list(list(value))
        # `None` and a blank string say the field holds nothing, which for a
        # list is the empty one — there is no content here to preserve.
        if value is None or (isinstance(value, str) and not value.strip()):
            return yaml_list([])
        # An inline list re-read from a note comes back as its raw `[a, b]`
        # source. Read it and re-render rather than pass it through, so what
        # reaches the note has been through the one renderer that knows how to
        # quote a flow item.
        if isinstance(value, str):
            items = parse_flow_list(value)
            return yaml_list(items) if items is not None else yaml_scalar(value)
        # Anything else is a value the field cannot hold, and `[]` would report
        # a write that erased it. Rendered as the scalar it is, it stays valid
        # YAML and reaches `kboat-validate` as the wrong type it is.
        return yaml_scalar(value)
    return yaml_scalar(value)


def _scalar_line(name: str, rendered: str) -> str:
    """`key: value`, or a bare `key:` for an empty value — matching how an empty
    scalar (`filed_date:`, an unset `summary:`) is written by hand. (An empty
    *string* renders as `""` via `yaml_scalar`, which is truthy, so this only
    bares genuinely-empty `None` values.)"""
    return f"{name}: {rendered}" if rendered else f"{name}:"


def _block_list_lines(field: Field, value: object) -> list[str]:
    """A block list's lines: `key:`, then one `- item` each.

    A string holding an inline sequence is read into its items and laid out —
    the reader hands an inline list back that way, and the note's own style is
    what decides the shape it is written in. Anything else has no items to lay
    out and goes back to the single-line rendering rather than to `[]`, the
    same rule `render_field` keeps: a write that reports success while erasing
    what it was given is the worse outcome either way.
    """
    if not isinstance(value, list):
        items = parse_flow_list(value) if isinstance(value, str) else None
        if items is None:
            return [_scalar_line(field.name, render_field(field, value))]
        value = items
    if not value:
        return [f"{field.name}: []"]  # an empty list stays a list (re-reads as [], not None)
    # An empty rendering is made explicit: a bare `-` is not an empty item, and
    # it reads back as `""` — so the note would come back from the write holding
    # something the write was never given, and settle only on the one after.
    rendered = [yaml_scalar(item) or '""' for item in value]
    return [f"{field.name}:"] + [f"  - {text}" for text in rendered]


def build_note(
    schema: NoteSchema,
    fields: Mapping[str, object],
    body: str = "",
    carried: Sequence[Entry] = (),
) -> str:
    """Assemble a note: frontmatter in `schema` field order, then `body`.

    Only keys present in `fields` are written; keys absent from the schema are
    appended after the ordered ones (rendered as plain scalars) so nothing is
    silently dropped.

    `carried` is frontmatter to put back **verbatim** rather than render —
    `parse_entries` output for the entries this write is not changing. A carried
    schema field keeps its canonical position; every other carried entry follows
    the rendered ones, in the relative order the note already had. `fields` wins
    over a carried entry for the same key, and a key carried twice keeps its last
    entry, matching which of a repeated key the reader (and Obsidian) reads.

    A carried entry that owns no key and would attach to the line above it goes
    first, before anything else — there is nothing for it to attach to there.
    Reordering is otherwise safe, but such a line takes its meaning from its
    neighbour, so placing it after a key would hand it to a key that never had
    it, and the write would invent a value it was never given.

    `body` is appended verbatim after a blank line when non-empty — a
    frontmatter-only note passes `body=""`.
    """
    last = {e.key: i for i, e in enumerate(carried) if e.key is not None}
    loose = [i for i, e in enumerate(carried) if e.key is None and continues_previous(e.lines[0])]
    emitted: set[str] = set()
    lines = ["---", *(line for i in loose for line in carried[i].lines)]
    for f in schema.fields:
        if f.name in fields:
            if f.kind is Kind.STR_LIST and f.list_style == "block":
                lines.extend(_block_list_lines(f, fields[f.name]))
            else:
                lines.append(_scalar_line(f.name, render_field(f, fields[f.name])))
        elif f.name in last:
            lines.extend(carried[last[f.name]].lines)
        else:
            continue
        emitted.add(f.name)
    for key in fields:
        if schema.get(key) is None:
            lines.append(_scalar_line(key, render_field(None, fields[key])))
            emitted.add(key)
    for i, entry in enumerate(carried):
        if entry.key is None:
            if i not in loose:
                lines.extend(entry.lines)
        elif entry.key not in emitted and last[entry.key] == i:
            lines.extend(entry.lines)
            emitted.add(entry.key)
    lines.append("---")
    out = "\n".join(lines) + "\n"
    body = body.strip("\n")
    if body:
        out += "\n" + body + "\n"
    return out


# --------- upsert: read existing, merge, stamp, write ---------


NOTES_HEADING = "## Notes"


_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _fenced_lines(lines: list[str]) -> set[int]:
    """The line indices inside a *balanced* code fence.

    A closing fence has to use the opener's own character, be at least as long,
    and carry no info string — otherwise documenting markdown inside markdown
    (a four-backtick block quoting a three-backtick one) reads as two fences and
    inverts what is inside.

    An opener with no closer does not open a fence here. Markdown would run it
    to the end of the document, but that hides the writer's own `## Notes`
    heading — and a heading that cannot be found is one that gets written again,
    one more per unattended run. Converging matters more than honouring a fence
    the human left unterminated.
    """
    inside: set[int] = set()
    opened_at: int | None = None
    marker = ""
    for i, line in enumerate(lines):
        match = _FENCE_RE.match(line)
        if match is None:
            continue
        fence, info = match.group(1), match.group(2)
        if opened_at is None:
            opened_at, marker = i, fence
        elif fence[0] == marker[0] and len(fence) >= len(marker) and not info.strip():
            inside.update(range(opened_at, i + 1))
            opened_at = None
    return inside


def split_notes_section(body: str) -> tuple[str, str | None]:
    """A `notes`-mode body as `(everything above the heading, the section)`.

    The section is None when the body has no heading, which is not the same as
    an empty section: a caller has to be able to tell "there is nowhere to put
    notes" from "the place to put them is empty".

    The heading is matched as a whole line and only outside a code fence.
    `### Notes on chapter 3`, a sentence naming the section, and a fenced block
    quoting the heading all contain it as a substring or a line, and splitting a
    body at one of those rearranges the reader's own prose into a shape they
    never wrote.
    """
    lines = split_lines(body)
    fenced = _fenced_lines(lines)
    for i, line in enumerate(lines):
        if i not in fenced and line.rstrip() == NOTES_HEADING:
            return "\n".join(lines[:i]).strip("\n"), "\n".join(lines[i + 1 :]).strip()
    return body.strip("\n"), None


def _compose_body(schema: NoteSchema, existing: str, authored: str) -> str:
    """The body to write, from what the note already holds and what was authored.

    The body mode says what the writer may *author*, never what it may delete: a
    `none` schema writes no body of its own but keeps prose a human put under the
    fence, and a `notes` schema owns its `## Notes` section only — anything above
    that heading is the human's and stays.
    """
    if schema.body != "notes":
        return (authored or existing).strip("\n")
    head, existing_notes = split_notes_section(existing)
    notes = authored.strip() or (existing_notes or "")
    section = f"{NOTES_HEADING}\n\n{notes}\n" if notes else NOTES_HEADING
    return f"{head}\n\n{section}" if head else section


def carried_entries(entries: Sequence[Entry], written: Collection[str]) -> list[Entry]:
    """The entries to put back verbatim: everything the write is not about.

    Filtered on what an entry is *about*, not on what decoded from it. An entry
    naming a written key that the reader cannot decode (`"url": x`, `url : x`) is
    still that key: carried, it would be re-emitted after the rendered value and
    win on YAML's last-key-wins, so the write would silently not take.

    This is the one predicate here that *deletes* a source line, and it does so on
    a deliberately loose match — which is why it is named rather than inlined, so
    a test can hold the real thing rather than a re-implementation of it.
    """
    return [
        e
        for e in entries
        if e.key not in written and not any(names_key(e.lines[0], k) for k in written)
    ]


def _empty_for(field: Field) -> object:
    return [] if field.kind is Kind.STR_LIST else None


# The characters Obsidian forbids in a filename — the naming rule every note
# type follows. A character that may not stand for itself is refused too, by the
# same predicate the renderer asks, so "a control character" is one rule here.
_FORBIDDEN_IN_A_NAME = frozenset('/\\:*?"<>|')


def _filename_slug(slug: object) -> str:
    """`slug` as the one path component it names, or `BadInputError`.

    The slug is interpolated into the note's path, and every caller assembles it
    — from a URL hash, an ASIN, or a name it chose. Anything the vault's naming
    rule does not allow in a filename is therefore not a slug at all but a record
    that would write outside the directory its type owns, or under a name the
    vault does not show. This is the single place every note type is written
    through, which is what makes one check enough.
    """
    if not isinstance(slug, str) or not slug.strip():
        raise BadInputError("record 'slug' must be a non-empty string")
    # Edge whitespace is refused rather than trimmed: the slug is the caller's
    # identifier for the note and comes back in the result, so a writer that
    # quietly wrote a different one would hand back a name for a file it did
    # not write.
    unusable = any(c in _FORBIDDEN_IN_A_NAME or is_unprintable(c) for c in slug)
    if slug != slug.strip() or slug.startswith(".") or unusable:
        raise BadInputError(f"record 'slug' is not a filename: {slug!r}")
    return slug


def upsert(
    schema: NoteSchema, vault: Path, record: Mapping[str, object], *, today: str
) -> dict[str, object]:
    """Create or update one note from a `{slug, fields, body?}` record.

    The record's `fields` are merged over the existing note (provided keys win,
    absent keys preserved), the schema's `created`/`refreshed` date fields are
    stamped, empty present-required fields are filled on create, and the body is
    preserved unless a new one is given.

    An update **re-renders only what it changes**. Every other frontmatter entry
    is put back as the note already had it, so a value the reader decodes only
    approximately — an inline list, a quoted string, a property no schema knows —
    cannot be degraded by a write that was never about it. That matters because
    these notes are rewritten unattended: a lossy round-trip would compound
    silently, once per run.

    A different `identity` value at an existing slug is a collision (never
    overwritten). Returns `{status, slug, path}`, or a `collision` record; a
    record that does not say a note — a slug that is no filename, a field name
    that is no property key — raises `BadInputError` and writes nothing.
    """
    slug = _filename_slug(record.get("slug"))
    fields_in = record.get("fields", {})
    provided: dict[str, object] = {}
    if isinstance(fields_in, Mapping):
        for k, v in fields_in.items():
            key = str(k)
            # A value the writer cannot render is kept as a quoted scalar, which
            # costs that field alone. A *name* has no such fallback: quoted, it
            # is outside the reader's grammar and the property it writes can
            # never be read back, so a record naming one is refused instead.
            if not is_plain_key(key):
                raise BadInputError(f"record field name is not a property key: {key!r}")
            provided[key] = v
    body_in = record.get("body", "")
    rel = f"{DIR_BY_TYPE[schema.type]}/{slug}.md"
    path = vault / DIR_BY_TYPE[schema.type] / f"{slug}.md"
    created = not path.exists()

    entries: list[Entry] = []
    existing_body = ""
    if not created:
        text = path.read_text(encoding="utf-8")
        entries = parse_entries(text)
        if schema.identity is not None:
            # The *last* entry naming the identity, and its value — one entry, so
            # the two cannot disagree. Last, because that is the one the reader
            # and Obsidian both take when a key repeats: reading the name off one
            # line and the value off another would clear a write against a value
            # the note does not actually hold.
            held = next(
                (e for e in reversed(entries) if names_key(e.lines[0], schema.identity)), None
            )
            old = held.value if held is not None and held.modelled else None
            new = provided.get(schema.identity)
            # An identity the reader cannot compare is not a licence to proceed:
            # this check exists to refuse a write, so it has to fail closed. What
            # decides it is comparability, not decodability — a `url` held as a
            # one-item list (what Obsidian writes when a property's type is set
            # to List) decodes perfectly well and still cannot be matched against
            # a string. An identity that is plainly empty is a blank to fill
            # rather than a claim to contradict, and an absent one leaves nothing
            # to refuse.
            unreadable = (
                isinstance(new, str)
                and held is not None
                and not isinstance(old, str)
                and not (held.modelled and old is None)
            )
            if unreadable or (isinstance(old, str) and isinstance(new, str) and old != new):
                return {
                    "status": "collision",
                    "reason": "unreadable_identity" if unreadable else "identity_differs",
                    "slug": slug,
                    "identity": schema.identity,
                    "existing": old,
                    "incoming": new,
                }
        existing_body = body_after_frontmatter(text)

    # `written` is the whole worklist: what the record supplies, plus the stamps
    # and create-time defaults the schema owns. Nothing else is re-rendered, so
    # every remaining entry the note already held goes back verbatim.
    written: dict[str, object] = dict(provided)
    for f in schema.fields:
        if f.stamp == "refreshed" or (f.stamp == "created" and created):
            written[f.name] = today
        elif created and f.present and f.name not in written:
            written[f.name] = f.default if f.default is not None else _empty_for(f)
    carried = carried_entries(entries, written)

    # A `none` schema never authors a body — that is what the mode declares — and
    # a blank one is not content, so neither replaces what the note already holds.
    supplied = str(body_in) if isinstance(body_in, str) and schema.body != "none" else ""
    body = _compose_body(schema, existing_body, supplied if supplied.strip() else "")
    atomic_write_text(path, build_note(schema, written, body, carried))
    return {"status": "created" if created else "updated", "slug": slug, "path": rel}
