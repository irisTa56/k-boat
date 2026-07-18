"""Pick's view of the shared frontmatter core.

The reader and the scoped writer live in `kboat.frontmatter`; this module only
adds the one pick-specific write — setting the `picked` flag — as a thin wrapper,
and re-exports the primitives the pick code imports.
"""

from __future__ import annotations

from kboat.frontmatter import FrontmatterError, Value, parse_frontmatter, set_field

__all__ = ["FrontmatterError", "Value", "parse_frontmatter", "set_picked"]


def set_picked(text: str, value: bool) -> str:
    """Set the top-level `picked:` line to `value`, inserting one after `blocked:`
    if a note predates the field."""
    return set_field(
        text,
        "picked",
        "true" if value else "false",
        insert_if_absent=True,
        insert_after="blocked",
    )
