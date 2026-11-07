---
name: kboat-vault-conventions
description: Shared conventions and the mechanical contract for writing any note into the K-Boat Obsidian vault (OBSIDIAN_VAULT_PATH) — URL-hash naming, frontmatter rules, the code-authoritative kboat.schema and its sync gate, the kboat.write.upsert write contract, Base-authoring discipline, and the vault's environment preconditions (the kboat-doctor check). Use when adding or changing a vault note type, writing a note into the vault, authoring a Base, or working out what kboat-doctor checks and why a precondition failed. Every vault writer defers here for how the vault works; a note type's own fields, semantics, and lifecycle live in the owning member's spec (K-Boat's in kboat-notes).
---

# Vault conventions

The Obsidian vault (`OBSIDIAN_VAULT_PATH`) is one shared database with more than one writer.
K-Boat writes its source, Kindle, repo, and review notes; the upstream feed-filter member writes its feed notes into the same vault.
These conventions are the rules-of-the-road every writer follows so the vault stays coherent — divergent naming, a Base that silently drops half its notes, or a validator blind to a note type would break the shared database.

This spec owns *how the vault works*, not *what any one note type is*.
A note type's own fields, semantics, and lifecycle live in the owning member's spec — K-Boat's in the `kboat-notes` skill.
The mechanical schema of every type is code-authoritative in `kboat.schema` (the `kboat` library); this spec explains the contract around it.

## Vault preconditions

The vault is one shared directory on an iCloud-synced volume, so before an unattended run reads or writes anything it has to establish that the vault is there and that its contents can be read.
`kboat-doctor` (in the `kboat` library) is that check: it prints every check as JSON on stdout with diagnostics on stderr, and exits 0 when nothing failed, 1 when anything did.
It is read-only apart from one probe file it creates and removes again.
The root, folder, readability, and eviction checks are vault-wide: they assert the shared vault is present and readable, and say whether it is fully local, `Feeds/` included, because a member's folder missing or unreadable means the vault is, whoever reads it.
Only `Questions.md` and the K-Boat-owned `Queue/`, `Reviews/`, and `PDFs/` narrow the set to the K-Boat routine, which is why the set as it stands is that routine's precondition and a second member wanting one needs its own.
The `environment` check is the one about the checkout rather than the vault, and it is narrowed to that routine as well: it asks only about the environments the routine runs.

The check runs once, before the phases, and a permission change can land on a vault it passed.
So a command that reads an input in the set holds it to the same rule rather than leaning on the check: tolerating a folder that is not there is exactly how a vault that failed to sync gets processed as though it were complete.

- A required folder that is **absent, not a directory, or refused** makes the command that reads it exit 1, and so does a `Questions.md` the daily pick cannot read — absent, not a file, refused, or not UTF-8, each of which loses the whole backlog.
  - It still prints its JSON report, naming the path and which of these it met in the entry the report already keeps for what it could not read, and it still processes and reports whatever else it could read.
  - It does so the first time, with no threshold, since none of these clears itself.
  - The commands are `kboat-lifecycle`, `kboat-pick` (`candidates` and `set`), `kboat-queue list`, `kboat-repos refresh` and `backfill`, `kboat-note migrate-slugs` and `list`, and `kboat-notebooks` (`sweep` and `resolve`); the skill that reads each one names the entry and says what to do with the rest of the report.
- A reader tells that exit from the vault lock's two by what is on stdout: the lock's are a `{"status": "locked", …}` record and an empty stdout ("Durability and the vault lock"), and this one is the command's own report.
- `kboat-validate` is the exception: it is report-only by design, and `--strict` already exits 1 on its `unreadable_dir` violation.
- Creating a missing folder belongs to declaring the note type rather than to the run.

The set is what a run cannot proceed without, not everything the vault holds.
An input a phase degrades over by design is deliberately out — the daily pick's `Daily/` notes are its ambient signal and it ranks without them, so their absence is not a precondition failure, whereas `Questions.md`, one of [the checks](references/preconditions.md#the-checks), is the deliberate signal the pick is steered by.
So `kboat-pick candidates` says nothing about an absent `Daily/` and reports a refused one, or a note in the look-back window it could not read, as an anomaly without failing.

The checks one by one, each with why it is drawn where it is, and the JSON report `kboat-doctor` prints are in [references/preconditions.md](references/preconditions.md).
Read it when working out what a check found or why a precondition failed.

## Naming

A content note is named by a URL hash, not by its title.
The filename is the first 12 hex characters of the SHA-256 of the note's **canonical** identity URL, e.g. `a1b2c3d4e5f6.md`.
Ask for it rather than hashing by hand: `kboat-note slug "<url>"` prints `{url, canonical_url, slug}` and is the single oracle every writer and every skill uses.
The same recipe (`kboat.naming.note_slug`) runs over the note's own `url` field whatever the note type, so one page cannot come to occupy two slugs — and the write contract recomputes it to verify a record before writing anything (below).

Hashing the canonical form is what makes the slug an answer about a *page* rather than about one link to it.
`kboat.canonical.canonical_url` lowercases the scheme and host, drops default ports and the fragment, strips tracking parameters, sorts the query, upper-cases percent-encodings, collapses duplicate path slashes, and normalizes the trailing slash.
So `…/post/`, `…/post?utm_source=x`, and `…/post#intro` all name one note.
Because the canonical form is a function of the URL and the URL is the note's immutable identity, the hash is stable: the file is never renamed and the readable title lives only in the `title` property, surfaced by the Base.

Which URL a note type *stores* is still that type's own decision; only the step from the stored `url` to the filename is shared.

- A source stores the URL it was queued with — for a GitHub blob or raw link, normalized to the rendered page or the download URL (`kboat-notes`).
- A repo note stores the constructed `https://github.com/<owner>/<repo>` that `kboat.repos.identity` derives from whatever was linked, and it always holds that constructed URL rather than the link that was queued.
  - That construction is **routing**, not naming: it answers which repo a URL is about, and the slug then follows from the stored `url` like any other note's.
  - The two must stay apart, because the routing collapses forms of a repository's URL that the oracle keeps distinct — a `.git` suffix, a `?tab=` query — so a repo note named from the link it was queued with would land under as many names as that URL has forms; which links are a repository is `kboat.repos.identity`'s answer, summarized in kboat-notes [Naming and de-dup](../kboat-notes/references/repo-note.md#naming-and-de-dup).
- A feed note stores the canonical URL its gather deduped on.

The slug names a file inside one note type's folder, so the namespace is per folder rather than vault-wide.
One page triaged into `Feeds/` and later ingested into `Sources/` therefore holds the same slug in both — the ordinary case, since every type hashes the same canonical URL, and not a clash between the two.

One consequence every writer handles: 48 bits is collision-resistant but not collision-free, so de-dup by reading the existing note's identity field, never by filename alone — which is exactly what the write contract's collision check does.

A note already in the vault under an older name is repaired by `kboat-note migrate-slugs`, a repair a human runs; [references/naming.md](references/naming.md) has what it moves, the conflicts it reports, and how it treats the vault lock.

Not every note is hash-named.
A Kindle note is named by its Amazon ASIN and a review report by its date (`YYYY-MM-DD.md`); both are stable ids like the hash, so those files are never renamed either and their readable label needs no title formula (the filename is already legible).
For a note named from a human-derived string rather than a hash, ASIN, or date, replace the Obsidian-forbidden characters `/ \ : * ? " < > |` with `-`.

## Frontmatter conventions

- Property keys and enum values use `snake_case`.
- A date-valued property carries a `_date` suffix (`added_date`, `filed_date`, `refreshed_date`) and uses `YYYY-MM-DD`.
- A list that must stay on one line is written inline (flow style, `topics: [a, b, c]`) so a mechanical rewrite can replace that field's single line without touching the rest; a list that need not be single-line is written block style.
  - The choice is the field's `list_style` in `kboat.schema`.
- Frontmatter is machine-written (see "The write contract"): field order, YAML quoting, and empty-value rendering are the writer's job, never hand-assembled.

## Schema authority and validation

Each note type's **mechanical** schema — the exact field names, their order, kinds, defaults, which fields are always-present booleans (the Base-filter invariant below), and the enum domains — is code-authoritative in `kboat.schema` as a `NoteSchema`, the single declaration both the note writers and the validator read.
The field tuple order there *is* the canonical frontmatter order.

The owning member's spec restates each type's field list as a `| Property | Meaning |` table for human semantics.
That restatement drifts, so `packages/kboat/tests/test_doc_schema_sync.py` (run in `qa:py`) asserts each table's field list and order equal the schema's `field_names()` — adding, removing, renaming, or reordering a field on one side without the other fails the commit.
The per-field prose (defaults, kinds, enums) is woven into the `Meaning` cells and is *not* machine-checked, so keep it accurate by hand.
When a field changes, update the owning spec's table and `kboat.schema` together.

`kboat-validate` checks every vault note against its schema and prints the violations as JSON: per-field (`missing_field`, `empty_required`, `not_bool` / `bad_enum` / `bad_date` / `not_list` / `not_int` / `not_str`), plus any cross-field rules the schema defines, `parse_error`, `repeated_key` (below), and `unreadable_dir` against a note directory the OS refused to list.
That last is a violation like any other, so it enters `violations`, `counts.total` and `counts.by_code`; what it leaves alone is `checked` and the stats, which keep their own meanings — `checked` counts the notes the pass could list and the stats the ones it could read, a note that would not parse being in the first and not the second.
What it adds is that a vault read in part stops reporting as a clean one, which is the reading a short backlog otherwise invites.

It is read-only and report-only by default (exit 0; `--strict` exits non-zero), so a routine runs it last and surfaces the violations as drift for a human to fix.
`--stats` adds a block of backlog-health counts, defined by the owning member over its own lifecycle predicates rather than over the schema; K-Boat's set is in `kboat-notes` ([Backlog stats](../kboat-notes/references/validation.md#backlog-stats)).
Stats never affect the exit code — they describe how the backlog is moving, not whether a note is well-formed.

`repeated_key` is reported against a key that more than one top-level line names, counting a line that names it in a shape the reader cannot decode (`"picked": x`, `picked : x`).
The reader takes the last of those lines while a human editing the note sees the first, and nothing in the note says which was meant.
So the in-place rewriters behind `kboat-lifecycle`, `kboat-pick set`, `kboat-repos refresh` and `kboat-note migrate-slugs` (`kboat.frontmatter.set_field` / `set_fields`) refuse to write such a key, and one named only on a line the reader cannot decode, rather than pick a line or add a second; each command reports that note as not written, and it stays so on every run until a human repairs it.
The note writer refuses the whole note instead, whichever key repeats ("The write contract" below).
The repair for `repeated_key` is deleting the line not meant; for the lone undecodable line, which `kboat-validate` shows as a `missing_field` ("The write contract" below), it is rewriting that line as a plain `key: value`.

## The write contract

A note is never hand-assembled.
The write is owned by `kboat.write.upsert(schema, vault, record, *, today)` in the `kboat` library, with a CLI wrapper `kboat-note write --type <t>` that reads a `{slug, fields, body?}` JSON record on stdin.
K-Boat's prose skills give a record to the CLI on stdin, in the form the root [CLAUDE.md](../../CLAUDE.md#environment) sets; a Python member such as feed-filter calls `upsert` directly.
`kboat-repos write` is a second CLI over the same `upsert`, differing in the record shape it accepts (a `gather` record plus the judged fields, rather than `{slug, fields, body?}`), in that its `fields` block writes only the keys it owns — dropping one the schema does not declare, one belonging to the human or the schema (`reading`, the date stamps), and one the writer sets itself from the record's top level, and reporting all three as `dropped_fields` — and in that it authors no body of its own; everything else below holds for it too.

The rest of the contract is in [references/write-contract.md](references/write-contract.md); read it before writing a note or changing a writer.

- [What `upsert` guarantees](references/write-contract.md#what-upsert-guarantees) from a `{slug, fields, body?}` record:
  - where the note is written, and the slugs and field names refused outright
  - what holds the slug deciding create versus merge
  - a note naming a key twice being refused
  - merge on update, and clearing a field with `null`
  - derived fields, always-present defaults on create, and date stamps
  - a value never being erased to fit its field, and what the writer emits reading back as itself
  - slug verification and the collision check
  - the body mode, and only what changes being re-rendered
  - the resurrection idiom the merge rule gives a member
- [A name is asked about through `kboat.io_utils`](references/write-contract.md#a-name-is-asked-about-through-kboatio_utils), never answered by `pathlib`.
- [A skill step that reads frontmatter from a note folder, or from one note by slug, does it through `kboat-note list`](references/write-contract.md#reading-notes-kboat-note-list), never a glob, `ls`, or a direct read.

## Durability and the vault lock

More than one writer runs against the vault, all on one Mac: the daily K-Boat routine, a feed-filter or forum run, and a human running a `kboat-*` command or editing in Obsidian.
Two mechanics keep the writers that go through the tools from losing each other's work; both live in the `kboat` library, so both cover exactly those writes and [What is outside](references/durability.md#what-is-outside) names the rest.
Both also take the **vault root as a precondition**: a tool creates the folders inside it, never the vault itself, so a mis-typed `--vault` or a misresolved `$OBSIDIAN_VAULT_PATH` is reported rather than grown into a second, empty vault that Obsidian never reads.

Each rule below is stated with its reasons and its edge cases in [references/durability.md](references/durability.md).

- [**Every write the tools make is durable and atomic.**](references/durability.md#atomic-writes)
- [**Every mutating run of the tools holds the vault lock.**](references/durability.md#the-vault-lock)
  - **The lock file lives outside the vault**, in `~/.k-boat/locks/` on every platform, or in `$KBOAT_LOCK_DIR` where that is set.
- [**There is no stale lock to recover, because the kernel releases it.**](references/durability.md#no-stale-lock) **Do not delete the lock file.**
- [How a writer takes it](references/durability.md#how-a-writer-takes-it):
  - **A read-only command takes no lock**, and **`kboat-doctor` takes none either**.
  - **One policy: wait a little, then refuse.**
  - **A refused caller reports and ends that step.**
  - **A lock that cannot be taken at all is not a refusal.**
- [What is outside](references/durability.md#what-is-outside) the unit these mechanics protect, one tool invocation writing one file's contents.

## Base-authoring discipline

A [Base](https://help.obsidian.md/bases) is a saved view over the vault's notes; how its filters are written decides whether it stays complete.

- **Filter only on always-present values.**
  - Every filter must be a plain boolean, or an `==` / `!=` over an always-present property — never a `!=` over a property that might be missing, and never a date-emptiness test.
- **Why.** Obsidian Bases excludes a note missing the property from a `!=` filter, so a `!=` over a sometimes-missing property silently drops those notes.
  - A view stays complete only because the filter booleans are written on every note at creation (the write contract's always-present defaults) — the create-time invariant, not booleanness alone, is what guarantees it.
- **Signal a date state with a column, not a filter.**
  - To tell a date-set state from a date-empty one (e.g. "distilled" vs "still ripe"), carry the date as a column the human reads rather than filtering on its emptiness.
- **Show the title through a formula for hash- or ASIN-named notes.**
  - The filename is an opaque hash or ASIN, so render the readable title with a formula (`title_link`, or whatever the view's lead column is called) rather than the filename.
  - A date-named note needs no formula: `file.name` is already legible.
  - Where that formula links is the Base's own call.
    - `file.asLink(note.title)` shows `title` as text but opens the hash-named file — the default, and the right one whenever the note itself is worth opening: it has a body, or fields the reader wants in full.
    - Point somewhere else when nothing behind the row is worth opening: a body-less note whose hidden fields are only provenance is better served by `link(url, …)` onto the page itself.
- **First view is the default.**
  - A Base shows [its first view on open](https://help.obsidian.md/bases/views), so order the day-to-day working view first.
- Column widths and other cosmetics are per-vault tweaks, not part of the authored Base.
