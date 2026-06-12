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

from kboat.frontmatter import yaml_list, yaml_scalar
from kboat.schema import Field, Kind, NoteSchema


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
        return yaml_list(list(value) if isinstance(value, list) else [])
    return yaml_scalar(value)


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
            lines.append(f"{f.name}: {render_field(f, fields[f.name])}")
    for key in fields:
        if schema.get(key) is None:
            lines.append(f"{key}: {render_field(None, fields[key])}")
    lines.append("---")
    out = "\n".join(lines) + "\n"
    body = body.strip("\n")
    if body:
        out += "\n" + body + "\n"
    return out
