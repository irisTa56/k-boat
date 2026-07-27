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

The vault is one shared directory on an iCloud-synced volume, so before an unattended run reads or writes anything it has to establish that the vault is there and that its contents are actually local.
`kboat-doctor` (in the `kboat` library) is that check: it prints every check as JSON on stdout with diagnostics on stderr, and exits 0 when nothing failed, 1 when anything did.
It is read-only apart from one probe file it creates and removes again.
The root, folder, and placeholder checks are vault-wide: they assert the shared vault is present and fully local, `Feeds/` included, because a member's folder missing or half-synced means the vault is, whoever reads it.
Only `Questions.md` and the K-Boat-owned `Queue/`, `Reviews/`, and `PDFs/` narrow the set to the K-Boat routine, which is why the set as it stands is that routine's precondition and a second member wanting one needs its own.

The check is stricter than any single phase, on purpose.
A phase tolerates a folder that is not there — the lifecycle CLI reads an absent `Kindles/` as no Kindle notes, the validator as nothing to validate — and that tolerance is exactly how a vault that failed to sync gets processed as though it were complete.
So a folder in the set is required even where a phase would have shrugged, and creating it belongs to declaring the note type rather than to the run.

The set is what a run cannot proceed without, not everything the vault holds.
An input a phase degrades over by design is deliberately out — the daily pick's `Daily/` notes are its ambient signal and it ranks without them, so their absence is not a precondition failure, whereas `Questions.md` below is the deliberate signal the pick is steered by.

- **The root exists** and is a directory. A missing root short-circuits the rest: every other check would only restate the same fact.
- **The root is writable** — the root itself, not each folder the run writes into. Proven by creating a uniquely-named probe file with `O_EXCL` and unlinking it: permission bits are not the whole story on a synced volume, so the check writes rather than inspects. A probe that cannot be removed again fails too, since a file left in the vault is itself drift. A single folder made read-only under a writable root therefore passes here and fails mid-run; the check is aimed at a vault that is absent or not mounted, which is the failure that takes the root with it.
- **The required folders exist**, and separately that **no required name is taken by a non-directory** (a file, or a dangling symlink). Two checks, because the first wants `mkdir` and the second wants the name freed first — `mkdir` on a taken name fails. The set is every note type's directory (from `kboat.schema`'s `DIR_BY_TYPE`, so declaring a new type requires its folder) plus `Queue/`, `Reviews/`, and `PDFs/` (named beside `DIR_BY_TYPE` in `kboat.schema`, so the layout has one home). The writer would create a missing one on first write, but the check does not defer to that: an absent folder is indistinguishable from a vault that has not synced, and auto-creating it is how a run comes to write into a half-synced vault.
- **The questions file exists** — `Questions.md` at the vault root, the daily pick's backlog.
- **No iCloud placeholder shadows a file** in the scanned set: the note directories and `PDFs/` (each recursively, since a placeholder in a subfolder hides a file just as completely — though not into a symlinked subdirectory, since one symlink loop would hang the check every run waits on), plus `Questions.md` by name — that one is reported by the `questions_file` check rather than `icloud_notes`, since from there an evicted backlog and an absent one are the same finding with different remedies. An evicted file leaves a `.<name>.icloud` placeholder where the file was, which means the vault is not fully synced locally. The vault root is not otherwise swept, so an evicted `Sources.base` is not caught — a Base is Obsidian's view, which no phase reads.

The placeholder check is split by what an eviction actually costs, because a doctor failure stops the whole routine and must not stop it over a file the routine never reads.

- A placeholder under a note directory (`Queue/`, `Reviews/`, and every `DIR_BY_TYPE` folder) **fails**. A run that walks past one silently processes a vault missing content it has no way to know about. Most of those directories hold the run's input; `Reviews/` earns its place differently — the distill pass *appends* to a dated report there, and an evicted one reads as absent, so the append would start a second file and the earlier sections would return as a sync conflict.
- A placeholder under `PDFs/` is a **warning** that does not fail. It is the one directory a run neither reads nor writes: the file is only ever uploaded at ingest, and distillation reads the content back from the notebook. The eviction costs the human their reading copy, which is not worth stopping a run over.

The report on stdout is a JSON object with `vault`, `ok` (true when nothing failed), `checks`, and `counts`.
Each entry in `checks` carries `name`, `status` (`ok`, `warning`, or `failed`), `detail`, `paths`, and `path_count` — every key on every entry, and `counts` likewise carries `total` plus one count per status even at zero, so a reader never has to decide whether an absent key means empty or means nothing.
A check's names live in its `paths` alone, never restated in `detail`, so a reader acting on one list is not left wondering whether the other holds more.
`paths` names at most five, with `path_count` giving the true total: the failure these checks exist for can evict thousands of files at once, and the caller is an unattended agent whose context both streams land in, so the report is bounded rather than answering a question about the vault with the vault itself.
A failing root is reported alone, so a short-circuited run has fewer entries but never fewer keys.
Every non-`ok` check is also written to stderr, one line per finding, so an unattended log shows the reason without parsing the JSON back.

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
`--stats` adds a block of backlog-health counts, defined by the owning member over its own lifecycle predicates rather than over the schema; K-Boat's set is in `kboat-notes` ("Backlog stats").
Stats never affect the exit code — they describe how the backlog is moving, not whether a note is well-formed.

## The write contract

A note is never hand-assembled.
The write is owned by `kboat.write.upsert(schema, vault, record, *, today)` in the `kboat` library, with a CLI wrapper `kboat-note write --type <t>` that reads a `{slug, fields, body?}` JSON record on stdin.
K-Boat's prose skills pipe a record to the CLI; a Python member such as feed-filter calls `upsert` directly.
`kboat-repos write` is a second CLI over the same `upsert`, differing in the record shape it accepts (a `gather` record plus the judged fields, rather than `{slug, fields, body?}`), in that its `fields` block writes only the keys it owns — dropping one the schema does not declare, one belonging to the human or the schema (`reading`, the date stamps), and one the writer sets itself from the record's top level, and reporting all three as `dropped_fields` — and in that it authors no body of its own; everything else below holds for it too.

From a `{slug, fields, body?}` record, `upsert` guarantees:

- The note is written at `<type-dir>/<slug>.md` (the directory is `DIR_BY_TYPE[type]`) in the schema's canonical field order, YAML quoting and empty-value rendering handled.
  - The slug names one file inside that directory and nothing else, so a record whose slug is not a filename — blank, opening with a dot, or carrying edge whitespace, a control character, or one of the `/ \ : * ? " < > |` the naming section forbids — is refused and written nowhere. Every slug a member writes is a URL hash, an ASIN, or a name it chose itself, so the rule costs a real one nothing and keeps an assembled record from reaching outside its type's folder.
  - A field name outside the property-key grammar — ASCII letters, digits and `_`, never opening with a digit, which `snake_case` already satisfies — is refused the same way, and the whole record with it. A wrong *value* is kept as a quoted scalar and reported by `kboat-validate`; a name has no such fallback, since quoting it puts the property outside what the reader can decode and so beyond both the next write and the validator. A property a human hand-added under a name of their own is a different matter — the write carries it back untouched (below).
- **Merge on update.** If the file is absent it is created; if present, the record's `fields` are merged over the existing note — provided keys win, absent keys are preserved — so a partial write (omitting a field, or `body`) preserves what it omits.
- **Always-present defaults on create.** A present field absent from the record is filled with its schema default (a boolean → `false`), so the Base-filter booleans are written on every note from creation. On *update* a field the note has lost is left lost, not backfilled — a write re-renders only what it changes (below), and a writer that filled in blanks it was not asked about would be re-rendering the whole note. Drift of that kind is `kboat-validate`'s to report and a human's to repair.
- **Date stamps.** A `created`-stamp field (e.g. `added_date`) is set to `today` on create and preserved after; a `refreshed`-stamp field is set on every write.
- **A value is never erased to fit its field.** A list field given something with no items to lay out keeps it as the scalar it is rather than writing `[]`, so a wrong type reaches `kboat-validate` as a `not_list` for a human to see instead of vanishing under a successful write. That works for an inline list too, whose valid form is itself a string: what the check accepts is a string the writer would read back as a sequence, not any string at all. A string that holds an inline sequence (`"[a, b]"` — what the reader hands back for one) is read into its items and written in the field's own style. What the writer will not do is put a value into the note bare when doing so could break the frontmatter block: a value it cannot render as its field's type is quoted, so a wrong one costs its own field and never the whole note.
  - A null item inside a list is written as an explicit empty item, in either style. It is the one value with no shape of its own to keep — nothing is lost by writing the empty string it reads back as, and a bare `-` (or the word `None` inline) would be a value the record never carried.
- **What the writer emits, it reads back as itself — and so does every other reader.** Re-writing a note the writer already wrote changes nothing but a refresh stamp, down to a value the field could not hold: kept as a quoted scalar, that scalar is what the next write is handed and what it emits again. A rendering that read back as something else would move the note once per unattended run with no edit behind it, and the drift would look like the vault's own.
  - The other readers are the point of the second half. A value stays inside its own property for Obsidian and for any YAML reader too, which is why a character that one of them would treat as the end of a line is escaped rather than written raw. Source titles, summaries, and repo descriptions are page text and model output; without that rule the tail of one arrives as a property of the note — including `url`, the identity the collision check is there to defend.
- **Collision check.** When the schema declares an `identity` field (e.g. `url`), an existing note at the same slug that cannot be shown to be the same note is a collision — returned as `{status: "collision", reason, …}` and written nowhere. This is the de-dup-by-identity rule the naming section relies on. Two reasons, because the check exists to refuse and so has to fail closed either way:
  - `identity_differs` — the note's identity value is plainly a different one.
  - `unreadable_identity` — the record names an identity but the note holds its own in a shape the reader cannot decode, so nothing can be compared. Repairing the note by hand is the only way forward; the writer will not guess.
- **Body.** The body *mode* is a fixed schema attribute (`NoteSchema.body`), not a record field: for a `verbatim` schema the record's `body` content is appended after the frontmatter, `notes` wraps it in a `## Notes` section, and `none` means the writer never authors one. An `upsert` always preserves the body already in the note, under every mode — `none` says K-Boat writes no body of its own, not that it may delete one a human added below the fence, and `notes` owns its `## Notes` section rather than the whole body.
- **Only what changes is re-rendered.** Every frontmatter entry the record does not write is put back exactly as the note held it, including one the reader can represent only approximately (an inline list, a quoted string) and one it cannot represent at all (a hyphenated or quoted key, a nested mapping, a block scalar). A note is rewritten *around* what the write is not about, so a property outside the schema — Obsidian's own, or a plugin's — survives a routine that rewrites the note daily. Every schema field keeps its canonical position, written or carried; anything else follows in the order the note already had. The exception is a line that owns no key of its own and would attach to the line above it — that goes first, where there is nothing for it to attach to, since anywhere else would hand it to a key that never had it.
  - An unrepresentable property is invisible to `kboat-validate`, which reads what the scanner models. If it collides with a schema field, expect a `missing_field` for that field: the note holds it in a shape the checks cannot read, which is drift worth seeing.
  - "Does not write" is per line, so a comment sharing a line with a field the record writes goes with it. Put a note to self on its own line, where it is an entry of its own and survives.

The merge rule gives a member a clean **resurrection** idiom: to re-surface a note it force-writes the fields it owns (e.g. a status boolean back to `false`) while omitting the fields a human owns, so the human's values survive the update.

## Durability and the vault lock

More than one writer runs against the vault, all on one Mac: the daily K-Boat routine, a feed-filter or forum run, and a human running a `kboat-*` command or editing in Obsidian.
Two mechanics keep the writers that go through the tools from losing each other's work; both live in the `kboat` library, so both cover exactly those writes and "What is outside" below names the rest.
Both also take the **vault root as a precondition**: a tool creates the folders inside it, never the vault itself, so a mis-typed `--vault` or a misresolved `$OBSIDIAN_VAULT_PATH` is reported rather than grown into a second, empty vault that Obsidian never reads.

**Every write the tools make is durable and atomic.**
Content goes to a sibling temp file in the same directory, is flushed and `fsync`ed, is renamed into place with `os.replace`, and the parent directory is `fsync`ed after the rename.
The rename is one atomic syscall, so a crash mid-write never leaves a truncated note behind and the iCloud daemon never picks up a half-written one; the two `fsync`s carry that past a process crash to a power loss, which would otherwise find the rename recorded with the content still in the page cache.
Every write the library makes goes through `kboat.io_utils.atomic_write_text` — the schema-driven note writer, the in-place frontmatter rewrites, and feed-filter's own config write alike.
A raise from it always means nothing was written, which is what lets every caller report a failure or leave an entry for the next run; the directory `fsync` runs after the rename has landed, so its own failure is swallowed rather than allowed to contradict that.
An existing file's permissions survive a rewrite; a file the writer *creates* gets the temp file's own mode (`0o600`), since choosing one would mean overriding the caller's umask.

**Every mutating run of the tools holds the vault lock.**
Durability alone does not stop two runs from interleaving: each write lands whole, but a run that read a note before another run rewrote it silently overwrites that rewrite when it writes its own version back.
So a process about to mutate the vault takes `kboat.lock.vault_lock(vault)` — an advisory `flock` on `<vault>/.kboat.lock`, whose contents are a JSON `{pid, started}` record each holder rewrites so a refusal can name who has the vault — and releases it on the way out, including on an exception.
The record is a diagnostic and nothing else; no decision is made from it.
The lock is vault-wide rather than per-note so no writer has to know which folders another touches; one scoped to the folders each writer uses today would have to be re-derived every time a note type moved.
Each tool holds it across its own read as well as its own writes, because a plan computed before another run's writes would act on notes that no longer call for it.

**There is no stale lock to recover, because the kernel releases it.**
An `flock` belongs to the open file description, so it goes when the file descriptor closes — including on a `SIGKILL`, an OOM kill or a panic.
An interrupted run therefore leaves no lock behind at all: nothing here needs a stale window, an age heuristic, a liveness check on a recorded pid, or a takeover.
**Do not delete `<vault>/.kboat.lock`.** It persists between holds — carrying the last holder's record, which is why it looks like leftover state and is not — because exclusion is an agreement about one inode: remove it mid-run and the next writer creates a different inode and locks that instead, leaving two runs writing at once.
Nothing ever needs it cleared, which is the difference from a design with a stale window a human has to break.
What buys that is a premise worth stating, because it is the one thing that would invalidate the design: **all contention is same-host on a local volume.**
`~/Library/Mobile Documents/…` is not a network mount but a local APFS directory with a file-provider sync extension, so `flock` there is ordinary APFS advisory locking — verified against this vault, including that a holder killed without releasing leaves the lock free.
A vault on a genuine network filesystem would need that re-checked, since `flock` over NFS or SMB is where the semantics stop holding.

- **A read-only command takes no lock**, so a query neither blocks nor is blocked — `kboat-lifecycle --dry-run`, `kboat-repos refresh --dry-run`, `kboat-pick candidates`, `kboat-queue list`, `kboat-validate`, and `kboat-recall`'s search all read a vault another run is writing.
  - **`kboat-doctor` takes none either**, though its writability probe does write. It creates and removes one uniquely-named file of its own and touches no note, and it runs to find out whether the vault can be written at all. Holding the lock first would be circular, and a pre-flight check that refused whenever a run was in progress would be useless exactly when it is wanted.
- **One policy: wait a little, then refuse.** Every writer takes the lock on the same terms — a few seconds' wait, then a refusal. There is no attended/unattended split, because the `kboat-*` CLIs are run both ways: by a person at a keyboard and by the scheduled routine. A person does not perceive the wait, and an expired one hands back the same holder record an immediate refusal would have.
  - What rules out refusing immediately is that holds are not all short. `kboat-repos refresh` keeps the lock across the `gh` fetch of every note in the catalogue, because its read, fetch and rewrite are one read-modify-write — so "just retry" is not advice a refused caller can act on, and a few seconds of patience covers the ordinary case of overlapping a single note write.
- **A refused caller reports and ends that step.** It prints `{"status": "locked", "holder": {pid, started, path}}` on stdout **in place of** its usual output, names the holding process on stderr, and exits non-zero without writing anything. Every `holder` key is always present and any may be null: the record is written just after the lock is taken, so a refusal landing in that window reads the previous holder's, and a crash mid-write leaves one that does not parse at all. A caller reads that record instead of the keys it came for, so it reports the refusal and ends that step rather than parsing on — that step, not a routine around it, whose later phases make their own attempts. A refusal belongs in the run summary rather than a notification, since the next run recovers on its own.
  - For feed-filter the entry is simply not written, and its never-lost contract carries it: nothing is recorded seen, so the next gather rediscovers it. That is why the wait matters more to it than to a K-Boat phase, whose work survives being deferred — the dispositions, the cooldown clock and the queue are all still on disk and every phase is idempotent.
- **A lock that cannot be taken at all is not a refusal.** A missing vault root, a denied iCloud tree, a filesystem that will not take an `flock`, or — only on the run that first creates the lock file — a vault root that cannot be written to, is reported on stderr with **no** `locked` record and an **empty stdout**, because there is no holder and nothing to come back for. The report-shaped CLIs (`kboat-lifecycle`, `kboat-pick set`, `kboat-repos refresh`) name it `vault lock unavailable: …`; the note writers fold it into their `write failed: …`, and feed-filter into its `error: …`. What is common to all of them is the shape, not the wording. Do not parse stdout, and do not retry: unlike a refusal this does not clear itself, so report it as needing a human and stop. `kboat-doctor` diagnoses two of its causes and not the rest: its `vault_root` and `vault_writable` checks cover a missing or unwritable root, but nothing there inspects the lock file, so a `.kboat.lock` that is a directory, *any* symlink (the open is `O_NOFOLLOW`, so a live one fails as surely as a dangling one), or one on a filesystem refusing `flock` leaves doctor reporting `ok` while every write fails. Read the stderr line rather than assuming a green doctor means the lock is fine. How an unattended run raises it belongs to the scheduled-task prompt, not here.

**What is outside.**
The unit these mechanics protect is one tool invocation writing one file's contents, and five things sit outside it.

- **A file an agent writes itself**, rather than through a `kboat` tool: the distillation review report in `Reviews/`, a rescued source's PDF in `PDFs/`, and the deletion of a drained `Queue/` capture. No tool holds the lock on their behalf, and in the routine each belongs to one phase of one run.
- **A change that spans two files.** Each write is atomic on its own; a pair of them is not. `kboat-repos refresh` adopting a rename writes the new note and then unlinks the old one, so a crash in between leaves both — reported by the next run's `kboat-validate` as two notes for one repo, for a human to merge.
- **Anything attached to a note's inode rather than its contents.** The atomic write renames a new file over the old one, so a rewrite preserves the note's permissions and nothing else: extended attributes, ACLs, a per-file `com.apple.macl` grant and the creation date all belong to the replaced inode, and a Finder tag on a note the routine rewrites daily will not last. A stray `<name>.md.<random>.tmp` is the same story from the other side — the cleanup runs on an exception, not on a `SIGKILL`, and no scanner globs it because they all read `*.md`.
- **A note saved in Obsidian while a run is writing.** Obsidian holds no lock and writes its own way, and `kboat-lifecycle` and `kboat-pick set` rewrite a whole note, so a hand-edit saved in the same moment loses one side or the other. The remedy is not a mechanism: do not hand-edit notes during a run, and the daily routine runs while nobody is at the keyboard.
- **The judgement an agent forms between its own read and the write it then asks for.** An agent reads a source note, decides a `summary` or a disposition from it, and calls `kboat-note write` some time later; the writer re-reads the note under the lock, so the body, the human's `reading` checkbox, and every field the record omits survive — but a field the record *does* carry overwrites whatever changed in that gap. It is the one lost update the lock does not close, narrowed to the fields one write names.

Closing the agent-level ones would mean exposing acquire and release as a tool of their own, for an agent to hold around a whole phase.

## Base-authoring discipline

A [Base](https://help.obsidian.md/bases) is a saved view over the vault's notes; how its filters are written decides whether it stays complete.

- **Filter only on always-present values.** Every filter must be a plain boolean, or an `==` / `!=` over an always-present property — never a `!=` over a property that might be missing, and never a date-emptiness test.
- **Why.** Obsidian Bases excludes a note missing the property from a `!=` filter, so a `!=` over a sometimes-missing property silently drops those notes. A view stays complete only because the filter booleans are written on every note at creation (the write contract's always-present defaults) — the create-time invariant, not booleanness alone, is what guarantees it.
- **Signal a date state with a column, not a filter.** To tell a date-set state from a date-empty one (e.g. "distilled" vs "still ripe"), carry the date as a column the human reads rather than filtering on its emptiness.
- **Show the title through a formula for hash- or ASIN-named notes.** The filename is an opaque hash or ASIN, so render the readable title with a formula (`title_link`, or whatever the view's lead column is called) rather than the filename. A date-named note needs no formula: `file.name` is already legible.
  - Where that formula links is the Base's own call. `file.asLink(note.title)` shows `title` as text but opens the hash-named file — the default, and the right one whenever the note itself is worth opening: it has a body, or fields the reader wants in full.
  - Point somewhere else when nothing behind the row is worth opening: a body-less note whose hidden fields are only provenance is better served by `link(url, …)` onto the page itself.
- **First view is the default.** A Base shows [its first view on open](https://help.obsidian.md/bases/views), so order the day-to-day working view first.
- Column widths and other cosmetics are per-vault tweaks, not part of the authored Base.
