"""Repo-note assembly over the shared frontmatter core.

The generic reader, renderer, and scoped writers live in `kboat.frontmatter`;
this module adds the repo-note specifics — the canonical field order, the
per-field rendering (which lists go inline, which fields are booleans, the
`stars` default), full-note assembly, and the multi-field in-place rewrite that
`refresh` uses — and re-exports the shared primitives the repos code and tests
import. Lists (`language`, `topics`, `domain`) are written inline (`topics: [a,
b]`), so every top-level field is one line and `refresh` can rewrite a field by
replacing that line.
"""

from __future__ import annotations

from collections.abc import Mapping

from kboat.frontmatter import (
    FrontmatterError,
    body_after_frontmatter,
    parse_frontmatter,
    yaml_list,
    yaml_scalar,
)
from kboat.frontmatter import set_fields as _set_rendered_fields

__all__ = [
    "FrontmatterError",
    "body_after_frontmatter",
    "build_repo_note",
    "parse_frontmatter",
    "set_fields",
    "yaml_scalar",
]

# Canonical frontmatter field order (open links -> reading checkbox -> GitHub
# metadata -> classification -> status -> routine dates). Mirrors the schema
# table in `kboat-notes` "Repo note".
FIELD_ORDER = (
    "type",
    "title",
    "url",
    "homepage",
    "reading",
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


def render_value(key: str, value: object) -> str:
    """Render one frontmatter value to its inline YAML form, keyed by field."""
    if key in ("language", "topics", "domain"):
        items: list[object] = list(value) if isinstance(value, list) else []
        return yaml_list(items)
    if key in ("reading", "archived"):
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

    Each key in `updates` must already exist as a top-level line (refresh targets
    always-present GitHub-derived fields); a missing key is a `FrontmatterError`,
    not a silent insert. The field order, every other field, and the body survive.
    """
    rendered = {key: render_value(key, value) for key, value in updates.items()}
    return _set_rendered_fields(text, rendered)
