"""Repo-note glue over the shared schema and writer.

The field order and per-field kinds live in `kboat.schema.REPO`; whole-note
assembly and single-field rendering live in `kboat.write` (`upsert` /
`render_field`). What is left repo-specific is the multi-field in-place rewrite
that `refresh` uses, which is this module, along with the shared primitives the
repos code and tests import from one place. Lists (`language`, `topics`,
`domain`) are inline (`topics: [a, b]`), so every top-level field is one line and
`refresh` can rewrite a field by replacing that line.
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
from kboat.write import render_field

__all__ = [
    "FrontmatterError",
    "body_after_frontmatter",
    "parse_frontmatter",
    "set_fields",
    "yaml_scalar",
]


def set_fields(text: str, updates: Mapping[str, object]) -> str:
    """Rewrite the named top-level frontmatter lines in place.

    Each key in `updates` must already exist as a top-level line (refresh targets
    always-present GitHub-derived fields); a missing key is a `FrontmatterError`,
    not a silent insert. The field order, every other field, and the body survive.
    """
    rendered = {key: render_field(REPO.get(key), value) for key, value in updates.items()}
    return _set_rendered_fields(text, rendered)
