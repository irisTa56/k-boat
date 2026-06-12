"""The K-Boat note schema, as a single declarative source.

This is the code-authoritative description of the *mechanical* frontmatter schema
for each note type — field names, order, kinds, defaults, which fields are
always-present booleans (the Obsidian Base filters depend on that), and the enum
domains. The `kboat-notes` skill remains the source of truth for what each field
*means* (semantics, lifecycle, the disposition composition rules) and points here
for the mechanical list.

Two consumers use it: `kboat validate` (checks every vault note against its
schema) and — from Slice 2 — the note writers (assemble a note in this field
order). The field tuple order *is* the canonical frontmatter order.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class Kind(Enum):
    ENUM = auto()  # one of a fixed set of strings
    BOOL = auto()  # true / false
    DATE = auto()  # YYYY-MM-DD
    STR = auto()  # any scalar string (URLs, ISO timestamps, loose dates)
    INT = auto()  # an integer
    STR_LIST = auto()  # a list of strings


@dataclass(frozen=True)
class Field:
    """One frontmatter field.

    `empty_ok` allows the key to be present with an empty value (`filed_date:`, an
    empty `summary`, `tags: []`). A BOOL is never `empty_ok` — that is exactly the
    always-present-boolean invariant the Base filters rely on. `default` and
    `list_style` are for the writers (Slice 2); validation ignores them.
    """

    name: str
    kind: Kind
    empty_ok: bool = False
    present: bool = True  # the key must exist (set False where the key may be absent)
    enum: tuple[str, ...] = ()
    default: object | None = None
    list_style: str = "block"  # "block" or "inline" (repo lists are inline)


@dataclass(frozen=True)
class NoteSchema:
    type: str
    fields: tuple[Field, ...]

    def field_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    def get(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name == name), None)


def _bool(name: str) -> Field:
    return Field(name, Kind.BOOL, default=False)


SOURCE = NoteSchema(
    "source",
    fields=(
        Field("type", Kind.ENUM, enum=("source",), default="source"),
        Field("title", Kind.STR),
        Field("reading_link", Kind.STR),
        Field("gemini_url", Kind.STR, empty_ok=True, present=False),
        _bool("reading"),
        _bool("distill"),
        _bool("keep"),
        _bool("dismiss"),
        Field("source_type", Kind.ENUM, enum=("web_page", "pdf")),
        Field("url", Kind.STR, empty_ok=True),  # null for an uploaded PDF
        Field("summary", Kind.STR, empty_ok=True),
        Field("topics", Kind.STR_LIST, empty_ok=True),
        Field("added_date", Kind.DATE),
        Field("filed_date", Kind.DATE, empty_ok=True),
        Field("distilled_date", Kind.DATE, empty_ok=True),
        _bool("blocked"),
        _bool("picked"),
        Field("notebooklm_id", Kind.STR, empty_ok=True, present=False),
        Field("notebooklm_url", Kind.STR, empty_ok=True, present=False),
        Field("tags", Kind.STR_LIST, empty_ok=True),
    ),
)

KINDLE = NoteSchema(
    "kindle",
    fields=(
        Field("type", Kind.ENUM, enum=("kindle",), default="kindle"),
        Field("title", Kind.STR),
        Field("reading_link", Kind.STR),
        Field("author", Kind.STR_LIST, empty_ok=True),
        Field("store_link", Kind.STR),
        Field("published", Kind.STR, empty_ok=True),
        Field("publisher", Kind.STR, empty_ok=True),
        _bool("reading"),
        _bool("finished"),
        _bool("distill"),
        Field("distilled_date", Kind.DATE, empty_ok=True),
        Field("added_date", Kind.DATE),
        Field("tags", Kind.STR_LIST, empty_ok=True),
    ),
)

REPO = NoteSchema(
    "repo",
    fields=(
        Field("type", Kind.ENUM, enum=("repo",), default="repo"),
        Field("title", Kind.STR),
        Field("url", Kind.STR),
        Field("homepage", Kind.STR, empty_ok=True),
        _bool("reading"),
        Field("description", Kind.STR, empty_ok=True),
        Field("language", Kind.STR_LIST, empty_ok=True, list_style="inline"),
        Field("topics", Kind.STR_LIST, empty_ok=True, list_style="inline"),
        Field("stars", Kind.INT, default=0),
        Field("archived", Kind.BOOL, default=False),
        Field("created_at", Kind.DATE, empty_ok=True),
        Field("last_commit", Kind.DATE, empty_ok=True),
        Field("license", Kind.STR, empty_ok=True),
        Field("role", Kind.STR),
        Field("domain", Kind.STR_LIST, empty_ok=True, list_style="inline"),
        Field("summary", Kind.STR, empty_ok=True),
        Field(
            "status",
            Kind.ENUM,
            enum=("recent", "active", "slow", "dormant", "archived", "unknown"),
        ),
        Field("added_date", Kind.DATE),
        Field("refreshed_date", Kind.DATE, empty_ok=True),
    ),
)

BY_TYPE: dict[str, NoteSchema] = {s.type: s for s in (SOURCE, KINDLE, REPO)}

# Which vault subdirectory holds each note type.
DIR_BY_TYPE: dict[str, str] = {"source": "Sources", "kindle": "Kindles", "repo": "Repos"}
