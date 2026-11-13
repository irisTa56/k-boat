"""Pick's view of the shared frontmatter core.

The reader and the scoped writer live in `kboat.frontmatter`; this module only
adds the pick-specific writes — the `picked` flag and the record of a body read —
as thin wrappers,
and re-exports the primitives the pick code imports.
"""

from kboat.frontmatter import (
    NOTE_READ_ERRORS,
    FrontmatterError,
    Value,
    parse_frontmatter,
    set_field,
)

__all__ = [
    "NOTE_READ_ERRORS",
    "FrontmatterError",
    "Value",
    "parse_frontmatter",
    "set_assessment",
    "set_picked",
]


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


def set_assessment(text: str, body_chars: int, act_early: bool) -> str:
    """Record a body read: `body_chars` and `act_early`, each rewritten in place,
    or inserted after `picked` (and `act_early` after `body_chars`) on a note no
    read has reached yet."""
    text = set_field(
        text, "body_chars", str(body_chars), insert_if_absent=True, insert_after="picked"
    )
    return set_field(
        text,
        "act_early",
        "yes" if act_early else "no",
        insert_if_absent=True,
        insert_after="body_chars",
    )
