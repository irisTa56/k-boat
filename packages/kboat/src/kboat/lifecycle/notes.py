"""Lifecycle's view of the shared frontmatter core.

The reader and the scoped writer live in `kboat.frontmatter`; this module only
adds the one lifecycle-specific write — stamping/clearing `filed_date` — as a thin
wrapper, and re-exports the primitives the lifecycle code imports.
"""

from __future__ import annotations

from kboat.frontmatter import (
    FrontmatterError,
    Scalar,
    Value,
    is_iso_date,
    parse_frontmatter,
    set_field,
)

__all__ = [
    "FrontmatterError",
    "Scalar",
    "Value",
    "is_iso_date",
    "parse_frontmatter",
    "set_filed_date",
]


def set_filed_date(text: str, value: str | None) -> str:
    """Rewrite the top-level `filed_date:` line — a `YYYY-MM-DD` string to stamp,
    or None to clear it back to `filed_date:`. The line must already exist."""
    return set_field(text, "filed_date", value)
