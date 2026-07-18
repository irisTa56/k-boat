---
name: vault-conventions
description: Shared conventions and the mechanical contract for writing any note into the K-Boat Obsidian vault (OBSIDIAN_VAULT_PATH) — URL-hash naming, frontmatter rules, the code-authoritative kboat.schema and its sync gate, the kboat.write.upsert write contract, and Base-authoring discipline. Use when adding or changing a vault note type, writing a note into the vault, or authoring a Base. Every vault writer defers here for how the vault works; a note type's own fields, semantics, and lifecycle live in the owning member's spec (K-Boat's in kboat-notes).
---

# Vault conventions

The Obsidian vault (`OBSIDIAN_VAULT_PATH`) is one shared database with more than one writer.
K-Boat writes its source, Kindle, repo, and review notes; the upstream feed-filter member writes its feed notes into the same vault.
These conventions are the rules-of-the-road every writer follows so the vault stays coherent — divergent naming, a Base that silently drops half its notes, or a validator blind to a note type would break the shared database.

This spec owns *how the vault works*, not *what any one note type is*.
A note type's own fields, semantics, and lifecycle live in the owning member's spec — K-Boat's in the `kboat-notes` skill.
The mechanical schema of every type is code-authoritative in `kboat.schema` (the `kboat` library); this spec explains the contract around it.

## Naming

A content note is named by a URL hash, not by its title.
The filename is the first 12 hex characters of the SHA-256 of the note's identity URL, hashed verbatim: `printf '%s' "<url>" | shasum -a 256 | cut -c1-12`, e.g. `a1b2c3d4e5f6.md`.
Use `printf '%s'`, not `echo`: a trailing newline would change every digest.
`shasum` is the macOS Perl tool, not `sha256sum`.

Which URL string is hashed is the owning note type's choice, but the recipe is the same everywhere.
K-Boat sources and repos hash the stored `url` verbatim, with no normalization; because that URL is the note's immutable identity the hash is stable, so the file is never renamed and the readable title lives only in the `title` property, surfaced by the Base.
A member that instead wants URL variants of one page collapsed to a single note canonicalizes the URL before hashing — harmless across members because each note type lives in its own vault folder.

One consequence every writer handles: 48 bits is collision-resistant but not collision-free, so de-dup by reading the existing note's identity field, never by filename alone — which is exactly what the write contract's collision check does.

Not every note is hash-named.
A Kindle note is named by its Amazon ASIN and a review report by its date (`YYYY-MM-DD.md`); both are stable ids like the hash, so those files are never renamed either and their readable label needs no title formula (the filename is already legible).
For a note named from a human-derived string rather than a hash, ASIN, or date, replace the Obsidian-forbidden characters `/ \ : * ? " < > |` with `-`.

## Frontmatter conventions

- Property keys and enum values use `snake_case`.
- A date-valued property carries a `_date` suffix (`added_date`, `filed_date`, `refreshed_date`) and uses `YYYY-MM-DD`.
- A list that must stay on one line is written inline (flow style, `topics: [a, b, c]`) so a mechanical rewrite can replace that field's single line without touching the rest; a list that need not be single-line is written block style. The choice is the field's `list_style` in `kboat.schema`.
- Frontmatter is machine-written (see "The write contract"): field order, YAML quoting, and empty-value rendering are the writer's job, never hand-assembled.

## Schema authority and validation

Each note type's **mechanical** schema — the exact field names, their order, kinds, defaults, which fields are always-present booleans (the Base-filter invariant below), and the enum domains — is code-authoritative in `kboat.schema` as a `NoteSchema`, the single declaration both the note writers and the validator read.
The field tuple order there *is* the canonical frontmatter order.

The owning member's spec restates each type's field list as a `| Property | Meaning |` table for human semantics.
That restatement drifts, so `packages/kboat/tests/test_doc_schema_sync.py` (run in `qa:py`) asserts each table's field list and order equal the schema's `field_names()` — adding, removing, renaming, or reordering a field on one side without the other fails the commit.
The per-field prose (defaults, kinds, enums) is woven into the `Meaning` cells and is *not* machine-checked, so keep it accurate by hand.
When a field changes, update the owning spec's table and `kboat.schema` together.

`kboat-validate` checks every vault note against its schema and prints the violations as JSON: per-field (`missing_field`, `empty_required`, `not_bool` / `bad_enum` / `bad_date` / `not_list` / `not_int` / `not_str`), plus any cross-field rules the schema defines and `parse_error`.
It is read-only and report-only by default (exit 0; `--strict` exits non-zero), so a routine runs it last and surfaces the violations as drift for a human to fix.

## The write contract

A note is never hand-assembled.
The write is owned by `kboat.write.upsert(schema, vault, record, *, today)` in the `kboat` library, with a CLI wrapper `kboat-note write --type <t>` that reads a `{slug, fields, body?}` JSON record on stdin.
K-Boat's prose skills pipe a record to the CLI; a Python member such as feed-filter calls `upsert` directly.

From a `{slug, fields, body?}` record, `upsert` guarantees:

- The note is written at `<type-dir>/<slug>.md` (the directory is `DIR_BY_TYPE[type]`) in the schema's canonical field order, YAML quoting and empty-value rendering handled.
- **Merge on update.** If the file is absent it is created; if present, the record's `fields` are merged over the existing note — provided keys win, absent keys are preserved — so a partial write (omitting a field, or `body`) preserves what it omits.
- **Always-present defaults on create.** A present field absent from the record is filled with its schema default (a boolean → `false`), so the Base-filter booleans are written on every note from creation.
- **Date stamps.** A `created`-stamp field (e.g. `added_date`) is set to `today` on create and preserved after; a `refreshed`-stamp field is set on every write.
- **Collision check.** When the schema declares an `identity` field (e.g. `url`), an existing note at the same slug whose identity value differs from the incoming one is a collision — returned as `{status: "collision", …}` and written nowhere. This is the de-dup-by-identity rule the naming section relies on.
- **Body.** The body *mode* is a fixed schema attribute (`NoteSchema.body`), not a record field: for a `verbatim` schema the record's `body` content is appended after the frontmatter, `notes` wraps it in a `## Notes` section, and `none` is frontmatter-only. An update that omits `body` preserves the existing body.

The merge rule gives a member a clean **resurrection** idiom: to re-surface a note it force-writes the fields it owns (e.g. a status boolean back to `false`) while omitting the fields a human owns, so the human's values survive the update.

## Base-authoring discipline

A [Base](https://help.obsidian.md/bases) is a saved view over the vault's notes; how its filters are written decides whether it stays complete.

- **Filter only on always-present values.** Every filter must be a plain boolean, or an `==` / `!=` over an always-present property — never a `!=` over a property that might be missing, and never a date-emptiness test.
- **Why.** Obsidian Bases excludes a note missing the property from a `!=` filter, so a `!=` over a sometimes-missing property silently drops those notes. A view stays complete only because the filter booleans are written on every note at creation (the write contract's always-present defaults) — the create-time invariant, not booleanness alone, is what guarantees it.
- **Signal a date state with a column, not a filter.** To tell a date-set state from a date-empty one (e.g. "distilled" vs "still ripe"), carry the date as a column the human reads rather than filtering on its emptiness.
- **Show the title through a formula for hash- or ASIN-named notes.** The filename is an opaque hash or ASIN, so render the readable title with a `title_link` formula — `file.asLink(note.title)` shows `title` as text but links to the (hash-named) file. A date-named note needs no formula: `file.name` is already legible.
- **First view is the default.** A Base shows [its first view on open](https://help.obsidian.md/bases/views), so order the day-to-day working view first.
- Column widths and other cosmetics are per-vault tweaks, not part of the authored Base.
