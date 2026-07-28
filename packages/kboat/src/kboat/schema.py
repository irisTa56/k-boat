"""The vault note schema and the vault's layout, as a single declarative source.

This is the code-authoritative description of the *mechanical* frontmatter schema
for each note type — field names, order, kinds, defaults, which fields are
always-present booleans (the Obsidian Base filters depend on that), and the enum
domains. The owning skill remains the source of truth for what each field *means*
(semantics, lifecycle) and points here for the mechanical list: `kboat-notes` for
the K-Boat types (`SOURCE`/`KINDLE`/`REPO`), `kboat-feed-notes` for the
feed-filter type (`FEED`). The shared contract around this schema — the sync gate
and `kboat-validate` — is the `kboat-vault-conventions` skill.

The validator (which checks every vault note against its schema) and the note
writers (which assemble a note in this field order) both read it; the field tuple
order *is* the canonical frontmatter order.

Alongside the schema it holds where the vault keeps things — `DIR_BY_TYPE` and the
non-schema paths beside it — read by every tool that resolves a vault path. That
belongs here for the same reason the field order does: one declaration, so two
tools cannot disagree about the same folder.
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
    stamp: str = (
        ""  # "created" (set to today on create, preserved after) / "refreshed" (every write)
    )


@dataclass(frozen=True)
class NoteSchema:
    type: str
    fields: tuple[Field, ...]
    # The frontmatter field whose value uniquely identifies the note, used to
    # detect a slug collision on update (None when the slug itself is the
    # identity, e.g. a Kindle ASIN).
    identity: str | None = None
    # How the note carries a body: "none" (frontmatter only), "verbatim" (free
    # markdown after the fence, e.g. Kindle highlights), or "notes" (a `## Notes`
    # section, e.g. a repo's human notes).
    body: str = "none"

    @property
    def url_named(self) -> bool:
        """Whether the filename is `kboat.naming.note_slug` of `identity`.

        Derived rather than declared per schema: it licenses the writer to
        recompute a slug and refuse a note that does not match, and it is what
        `kboat-note migrate-slugs` scans, so a new type that forgot to opt in
        would lose both silently. The identity field of a URL-named type is
        called `url` — the vault's own convention, which `kboat-validate` and
        every writer already read by that name — so declaring one under another
        name is the case to revisit this with. A Kindle note's ASIN slug is an
        id, not a hash of anything, so nothing there can be recomputed.
        """
        return self.identity == "url"

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
        Field("added_date", Kind.DATE, stamp="created"),
        Field("filed_date", Kind.DATE, empty_ok=True),
        Field("distilled_date", Kind.DATE, empty_ok=True),
        _bool("blocked"),
        _bool("picked"),
        Field("notebooklm_id", Kind.STR, empty_ok=True, present=False),
        Field("notebooklm_url", Kind.STR, empty_ok=True, present=False),
        Field("tags", Kind.STR_LIST, empty_ok=True, list_style="inline"),
    ),
    identity="url",
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
        Field("added_date", Kind.DATE, stamp="created"),
        Field("tags", Kind.STR_LIST, empty_ok=True),
    ),
    body="verbatim",
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
            # `unknown` is the enum's no-data member, so a create with no `status`
            # (a record assembled without one) yields a valid note rather than an
            # empty value the field does not allow.
            default="unknown",
        ),
        Field("added_date", Kind.DATE, stamp="created"),
        Field("refreshed_date", Kind.DATE, empty_ok=True, stamp="refreshed"),
    ),
    identity="url",
    body="notes",
)

FEED = NoteSchema(
    "feed",
    fields=(
        Field("type", Kind.ENUM, enum=("feed",), default="feed"),
        Field("title", Kind.STR),
        Field("url", Kind.STR),
        _bool("read"),
        _bool("shelved"),
        _bool("dismissed"),
        _bool("wall"),
        Field("feed_kind", Kind.ENUM, enum=("article", "forum")),
        Field("site_id", Kind.STR),
        Field("summary", Kind.STR, empty_ok=True),
        Field("added_date", Kind.DATE, stamp="created"),
    ),
    identity="url",
)

BY_TYPE: dict[str, NoteSchema] = {s.type: s for s in (SOURCE, KINDLE, REPO, FEED)}

# Which vault subdirectory holds each note type.
DIR_BY_TYPE: dict[str, str] = {
    "source": "Sources",
    "kindle": "Kindles",
    "repo": "Repos",
    "feed": "Feeds",
}

# The vault paths that hold no schema-backed note, named here beside
# `DIR_BY_TYPE` so the layout has one home: a second copy of one of these is a
# second answer to where the vault keeps something, and the two diverge silently.
QUEUE_DIR = "Queue"  # the ingest inbox, one capture note per URL
REVIEWS_DIR = "Reviews"  # the distillation reports
PDFS_DIR = "PDFs"  # the downloaded file for each PDF source
QUESTIONS_FILE = "Questions.md"  # the daily pick's open-questions backlog, at the root
# Obsidian's Daily Notes folder. Read by the daily pick as its ambient interest
# signal and optional by design — the pick ranks without it — so unlike the paths
# above it is not a `kboat-doctor` precondition.
DAILY_DIR = "Daily"
