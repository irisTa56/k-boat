"""Repo-note glue over the shared schema and writer.

The field order and per-field kinds live in `kboat.schema.REPO`; assembly and
single-field rendering live in `kboat.write` (`build_note` / `render_field`). This
module is the repo-note-specific glue — the full-note builder (frontmatter plus a
`## Notes` body) and the multi-field in-place rewrite that `refresh` uses — and
re-exports the shared primitives the repos code and tests import. Lists
(`language`, `topics`, `domain`) are inline (`topics: [a, b]`), so every top-level
field is one line and `refresh` can rewrite a field by replacing that line.
"""

from __future__ import annotations

from collections.abc import Mapping

from kboat.frontmatter import (
    FrontmatterError,
    body_after_frontmatter,
    parse_frontmatter,
    yaml_scalar,
)
from kboat.frontmatter import set_fields as _set_rendered_fields
from kboat.schema import REPO
from kboat.write import build_note, render_field, split_notes_section

__all__ = [
    "FrontmatterError",
    "body_after_frontmatter",
    "build_repo_note",
    "parse_frontmatter",
    "set_fields",
    "split_notes_section",
    "yaml_scalar",
]


def build_repo_note(fields: Mapping[str, object], notes_body: str = "") -> str:
    """Assemble a full repo note: schema-ordered frontmatter + a `## Notes` section.

    `notes_body` is the content under `## Notes` (may be empty). Unknown keys in
    `fields` are appended after the ordered ones so nothing is silently dropped.
    """
    nb = notes_body.strip("\n")
    body = f"## Notes\n\n{nb}\n" if nb else "## Notes"
    return build_note(REPO, fields, body)


def set_fields(text: str, updates: Mapping[str, object]) -> str:
    """Rewrite the named top-level frontmatter lines in place.

    Each key in `updates` must already exist as a top-level line (refresh targets
    always-present GitHub-derived fields); a missing key is a `FrontmatterError`,
    not a silent insert. The field order, every other field, and the body survive.
    """
    rendered = {key: render_field(REPO.get(key), value) for key, value in updates.items()}
    return _set_rendered_fields(text, rendered)
