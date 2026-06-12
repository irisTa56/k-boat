"""Schema-driven note assembly.

`build_note` renders a note's frontmatter in its schema's field order (block lists
multi-line, inline lists in flow style), plus an optional body appended verbatim.
`render_field` renders one value to its single-line YAML form by the field's kind
— the one place per-field rendering lives, shared by `build_note` and the repos
`set_fields` rewriter. The schema (`kboat.schema`) is the field order and kinds;
this module is how a note is written from them.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from kboat.frontmatter import (
    body_after_frontmatter,
    parse_frontmatter,
    yaml_list,
    yaml_scalar,
)
from kboat.io_utils import atomic_write_text
from kboat.schema import DIR_BY_TYPE, Field, Kind, NoteSchema


def render_field(field: Field | None, value: object) -> str:
    """Render one value to its inline YAML text, by the field's kind.

    An unknown field (`None`) falls back to a plain scalar. This always returns a
    single line: a block list is rendered multi-line by `build_note`, not here, so
    `render_field` only sees inline-style lists.
    """
    if field is None:
        return yaml_scalar(value)
    if field.kind is Kind.BOOL:
        return "true" if value else "false"
    if field.kind is Kind.INT:
        if value is not None:
            return str(value)
        return str(field.default) if field.default is not None else "0"
    if field.kind is Kind.STR_LIST:
        if isinstance(value, list):
            return yaml_list(list(value))
        # An inline list re-read from a note parses as its raw `[a, b]` string;
        # pass it through unchanged rather than dropping it to `[]`.
        if isinstance(value, str):
            return value
        return yaml_list([])
    return yaml_scalar(value)


def _scalar_line(name: str, rendered: str) -> str:
    """`key: value`, or a bare `key:` for an empty value — matching how an empty
    scalar (`filed_date:`, an unset `summary:`) is written by hand. (An empty
    *string* renders as `""` via `yaml_scalar`, which is truthy, so this only
    bares genuinely-empty `None` values.)"""
    return f"{name}: {rendered}" if rendered else f"{name}:"


def _block_list_lines(name: str, value: object) -> list[str]:
    items = list(value) if isinstance(value, list) else []
    if not items:
        return [f"{name}:"]
    return [f"{name}:"] + [f"  - {yaml_scalar(item)}" for item in items]


def build_note(schema: NoteSchema, fields: Mapping[str, object], body: str = "") -> str:
    """Assemble a note: frontmatter in `schema` field order, then `body`.

    Only keys present in `fields` are written; keys absent from the schema are
    appended after the ordered ones (rendered as plain scalars) so nothing is
    silently dropped. `body` is appended verbatim after a blank line when
    non-empty — a frontmatter-only note passes `body=""`.
    """
    lines = ["---"]
    for f in schema.fields:
        if f.name not in fields:
            continue
        if f.kind is Kind.STR_LIST and f.list_style == "block":
            lines.extend(_block_list_lines(f.name, fields[f.name]))
        else:
            lines.append(_scalar_line(f.name, render_field(f, fields[f.name])))
    for key in fields:
        if schema.get(key) is None:
            lines.append(_scalar_line(key, render_field(None, fields[key])))
    lines.append("---")
    out = "\n".join(lines) + "\n"
    body = body.strip("\n")
    if body:
        out += "\n" + body + "\n"
    return out


# --------- upsert: read existing, merge, stamp, write ---------


def _existing_body(text: str, schema: NoteSchema) -> str:
    if schema.body == "notes":
        _, sep, tail = body_after_frontmatter(text).partition("## Notes")
        return tail.strip() if sep else ""
    if schema.body == "verbatim":
        return body_after_frontmatter(text).strip("\n")
    return ""


def _render_body(schema: NoteSchema, content: str) -> str:
    content = content.strip("\n")
    if schema.body == "notes":
        return f"## Notes\n\n{content}\n" if content else "## Notes"
    if schema.body == "verbatim":
        return content + "\n" if content else ""
    return ""


def _empty_for(field: Field) -> object:
    return [] if field.kind is Kind.STR_LIST else None


def upsert(
    schema: NoteSchema, vault: Path, record: Mapping[str, object], *, today: str
) -> dict[str, object]:
    """Create or update one note from a `{slug, fields, body?}` record.

    The record's `fields` are merged over the existing note (provided keys win,
    absent keys preserved), the schema's `created`/`refreshed` date fields are
    stamped, empty present-required fields are filled on create, and the body is
    preserved unless a new one is given. A different `identity` value at an
    existing slug is a collision (never overwritten). Returns `{status, slug,
    path}`, or a `collision` record.
    """
    slug = record["slug"]
    fields_in = record.get("fields", {})
    provided: dict[str, object] = {}
    if isinstance(fields_in, Mapping):
        for k, v in fields_in.items():
            provided[str(k)] = v
    body_in = record.get("body", "")
    rel = f"{DIR_BY_TYPE[schema.type]}/{slug}.md"
    path = vault / DIR_BY_TYPE[schema.type] / f"{slug}.md"
    created = not path.exists()

    existing: dict[str, object] = {}
    existing_body = ""
    if not created:
        text = path.read_text(encoding="utf-8")
        for key, value in parse_frontmatter(text).items():
            existing[key] = value
        if schema.identity is not None:
            old, new = existing.get(schema.identity), provided.get(schema.identity)
            if isinstance(old, str) and isinstance(new, str) and old != new:
                return {
                    "status": "collision",
                    "slug": slug,
                    "identity": schema.identity,
                    "existing": old,
                    "incoming": new,
                }
        existing_body = _existing_body(text, schema)

    merged: dict[str, object] = {**existing, **provided}
    for f in schema.fields:
        if f.stamp == "refreshed" or (f.stamp == "created" and created):
            merged[f.name] = today
        elif created and f.present and f.name not in merged:
            merged[f.name] = f.default if f.default is not None else _empty_for(f)

    new_body = body_in if isinstance(body_in, str) and body_in.strip() else existing_body
    atomic_write_text(path, build_note(schema, merged, _render_body(schema, new_body)))
    return {"status": "created" if created else "updated", "slug": slug, "path": rel}
