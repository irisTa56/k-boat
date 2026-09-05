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
The root, folder, readability, and placeholder checks are vault-wide: they assert the shared vault is present, readable, and fully local, `Feeds/` included, because a member's folder missing, unreadable, or half-synced means the vault is, whoever reads it.
Only `Questions.md` and the K-Boat-owned `Queue/`, `Reviews/`, and `PDFs/` narrow the set to the K-Boat routine, which is why the set as it stands is that routine's precondition and a second member wanting one needs its own.

The check is stricter than any single phase, on purpose.
A phase tolerates a folder that is not there — the lifecycle CLI reads an absent `Kindles/` as no Kindle notes, the validator as nothing to validate — and that tolerance is exactly how a vault that failed to sync gets processed as though it were complete.
So a folder in the set is required even where a phase would have shrugged, and creating it belongs to declaring the note type rather than to the run.

The set is what a run cannot proceed without, not everything the vault holds.
An input a phase degrades over by design is deliberately out — the daily pick's `Daily/` notes are its ambient signal and it ranks without them, so their absence is not a precondition failure, whereas `Questions.md` below is the deliberate signal the pick is steered by.

- **The root exists** and is a directory.
  - Absent, or a name held by something that is not one, short-circuits the rest: every other check would only restate the same fact.
  - A root the vault **refuses to read** does not short-circuit, and this is the third answer rather than a shade of the first.
    - Writability and readability each have their own finding about such a root, and a run that stopped would report none of them — telling a human that a vault which is there and merely unreadable is a vault that is gone.
- **The root is writable** — the root itself, not each folder the run writes into.
  - Proven by creating a uniquely-named probe file with `O_EXCL` and unlinking it: permission bits are not the whole story on a synced volume, so the check writes rather than inspects.
  - A probe that cannot be removed again fails too, since a file left in the vault is itself drift.
  - A single folder made read-only under a writable root therefore passes here and fails mid-run; the check is aimed at a vault that is absent or not mounted, which is the failure that takes the root with it.
- **The required folders exist**, and separately that **no required name is taken by a non-directory** (a file, or a dangling symlink).
  - Two checks, because the first wants `mkdir` and the second wants the name freed first — `mkdir` on a taken name fails.
  - The set is every note type's directory (from `kboat.schema`'s `DIR_BY_TYPE`, so declaring a new type requires its folder) plus `Queue/`, `Reviews/`, and `PDFs/` (named beside `DIR_BY_TYPE` in `kboat.schema`, so the layout has one home).
  - The writer would create a missing one on first write, but the check does not defer to that: an absent folder is indistinguishable from a vault that has not synced, and auto-creating it is how a run comes to write into a half-synced vault.
- **The questions file exists** — `Questions.md` at the vault root, the daily pick's backlog.
- **Every scanned directory can be read**, as `readable_notes` and `readable_assets`.
  - No scan can stand in for this.
    - A directory the OS refuses to list is what a scan reads as an empty one unless it was written not to: `Path.glob` swallows the refusal, and `is_dir()` still answers `True` because that `stat` goes through the parent.
  - Within the scanned set, it is the only place an unreadable folder is reported at all for the scans not yet under the rule, and what stops the run before each of the others reports it separately.
  - Outside that set nothing reports one.
    - `Daily/` is the case — globbed by the daily pick and deliberately no precondition of it — so an unreadable `Daily/` costs exactly what an absent one does, and a scan there cannot lean on this check.
  - The pair splits by cost on the same rule as the placeholder pair below, and by what a refusal actually costs rather than by which directory it is in.
    - A note directory **fails** however it is unreadable: its listing is a phase's input.
    - An asset directory that is unlistable but still **traversable** only warns.
      - No phase lists `PDFs/` — ingest writes one path and the slug migration probes one by name — so nothing is affected, and a failure would stop the routine over it.
    - The **asset directory itself** failing to be traversable is the exception, because there the per-name probes do answer wrongly: they read "absent" for a file that is there, so the slug migration moves a note away from a PDF it takes for gone, with `reading_link` retargeted at nothing.
      - That is `_pdf_state`'s refusal-blindness, which is not this check's to fix — but it is why this one state is not a warning.
    - A directory *below* it warns whatever its mode: the probes name `PDFs/<slug>.pdf` at the top level, so nothing there is affected, and failing would stop the whole routine over a folder no phase reads.
  - The scan is recursive and the failure deliberately wider than a phase's input: a subfolder under a note directory is one nothing lists, and it fails all the same, because what the check establishes is that the vault can be read rather than that today's phases happened to reach everything in it.
  - A directory that goes away mid-scan is not a refusal and only **warns**: the walk listed a parent and the child was gone by the time it descended, which clears itself before anyone can act.
    - The refusals name their `strerror` in `detail`, so the two are told apart without parsing a name out of `paths`.
- **No iCloud placeholder shadows a file** in the scanned set: the note directories and `PDFs/`, each recursively, plus `Questions.md` by name.
  - Recursively, since a placeholder in a subfolder hides a file just as completely — though not into a symlinked subdirectory, since one symlink loop would hang the check every run waits on.
  - `Questions.md` is reported by the `questions_file` check rather than `icloud_notes`, since from there an evicted backlog and an absent one are the same finding with different remedies.
  - An evicted file leaves a `.<name>.icloud` placeholder where the file was, which means the vault is not fully synced locally.
  - A placeholder sitting beside its own present file is not that and is reported by nothing: the file is here, so calling it evicted would tell a reader the wait is on a download that already happened, in the same report that shows the file was read.
    - That is the file-before-placeholder precedence the writers apply when they claim a name, asked of the reporting side by `kboat.io_utils.evictions` so one rule covers both.
    - It has a second half, and skipping the pair is only safe with it: a writer that renames or unlinks the file makes that stub a lone placeholder, which fails `icloud_notes` and stops the routine every day out of a report that never mentioned it.
    - So the side that breaks the pair names what it left. There are three such sides, and a writer that vacates a name is one whether it renames or deletes.
      - `migrate-slugs` puts it in the row's `detail` and `kboat-repos refresh` on the `adopted` entry as `stranded`, both via `kboat.io_utils.stranded_stub` (the reporting probe described under "The write contract"), each passing on a could-not-tell as itself.
      - `kboat-ingest` is the third: it deletes a drained `Queue/` capture, and `Queue/` is a note directory, so a stub left there fails `icloud_notes` exactly as one under `Sources/` would.
        - That deletion is an agent's rather than a tool's, so the check is prose in `kboat-ingest` rather than a call.
    - Removing the stub is a human's, deliberately: deleting a placeholder is how a file leaves iCloud.
  - The sweep is only as complete as `readable_notes` and `readable_assets`: a directory that could not be listed holds no findings for this check either, so an `icloud_notes` of `ok` beside a failing readability check says nothing was found rather than that nothing is there.
  - The vault root is not otherwise swept, so an evicted `Sources.base` is not caught — a Base is Obsidian's view, which no phase reads.

The placeholder check is split by what an eviction actually costs, because a doctor failure stops the whole routine and must not stop it over a file the routine never reads.

- A placeholder under a note directory (`Queue/`, `Reviews/`, and every `DIR_BY_TYPE` folder) **fails**.
  - A run that walks past one silently processes a vault missing content it has no way to know about.
  - Most of those directories hold the run's input; `Reviews/` earns its place differently — the distill pass *appends* to a dated report there, and an evicted one reads as absent, so the append would start a second file and the earlier sections would return as a sync conflict.
- A placeholder under `PDFs/` is a **warning** that does not fail.
  - It is the one directory a run neither reads nor writes: the file is only ever uploaded at ingest, and distillation reads the content back from the notebook.
  - The eviction costs the human their reading copy, which is not worth stopping a run over.

The report on stdout is a JSON object with `vault`, `ok` (true when nothing failed), `checks`, and `counts`.
Each entry in `checks` carries `name`, `status` (`ok`, `warning`, or `failed`), `detail`, `paths`, and `path_count` — every key on every entry, and `counts` likewise carries `total` plus one count per status even at zero, so a reader never has to decide whether an absent key means empty or means nothing.
A check's names live in its `paths` alone, never restated in `detail`, so a reader acting on one list is not left wondering whether the other holds more.
`paths` names at most five, with `path_count` giving the true total: the failure these checks exist for can evict thousands of files at once, and the caller is an unattended agent whose context both streams land in, so the report is bounded rather than answering a question about the vault with the vault itself.
A **short-circuiting** root failure — absent, or the name held by a non-directory — is reported alone, so that run has fewer entries but never fewer keys.
Every non-`ok` check is also written to stderr, one line per finding, so an unattended log shows the reason without parsing the JSON back.

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
  - The two must stay apart, because the routing deliberately collapses a `/blob/<ref>/README.md` link onto its repository — right for cataloguing the repo, and wrong for a file inside it, which is ingested as a source of its own.
- A feed note stores the canonical URL its gather deduped on.

The slug names a file inside one note type's folder, so the namespace is per folder rather than vault-wide.
One page triaged into `Feeds/` and later ingested into `Sources/` therefore holds the same slug in both — the ordinary case now that every type hashes the same canonical URL, and not a clash between the two.

One consequence every writer handles: 48 bits is collision-resistant but not collision-free, so de-dup by reading the existing note's identity field, never by filename alone — which is exactly what the write contract's collision check does.

A note already in the vault under an older name is repaired by `kboat-note migrate-slugs --dry-run|--apply`, which reports every URL-named note whose filename is not the slug its `url` names and, on `--apply`, renames it.
A PDF source is a pair: `PDFs/<slug>.pdf` and the note's `reading_link` link to it are both derived from the slug, so the file and the link move with the note or nothing does.
Only the filename inside that link is rewritten — a PDF++ page or highlight subpath is where the reader had got to, and it is carried across.
Several things make a row a conflict, all reported and skipped and never overwritten, and the row's `detail` names which.
A row that is **not** a conflict can carry one too: an `--apply` that vacates a name a stale stub sits beside — the note's own, its PDF's, or both — says so there, since the note's slug then matches and no later pass revisits it.
Removing that stub is a human's, deliberately — deleting a placeholder is how a file leaves iCloud.
They group by who clears them, which is what a reader triaging a dry run needs, and the groups are the contract rather than their number.

- **The file has to come back**, and then a re-run moves the pair: a note iCloud has evicted at the target name, or a source's PDF it has evicted at the source name — or at the target name while something still holds the source.
  - Neither can be merged with or even opened meanwhile, so neither is a human's to resolve.
  - An evicted PDF at the target with **nothing** at the source is not a conflict: the pair is already across and one rename from done, so refusing the row would strand it for good.
- **A human merges the two**: an existing note or PDF at a target name, or a slug two notes both want.
- **A human repairs the note**: a `reading_link` that names the note's PDF in a shape the tool cannot rewrite, which would dangle if the pair moved.
- **The vault is what needs looking at**: a target name it refuses to let the pass read at all, or one held by something that is not a note — a dangling symlink being the one that occurs, which nothing frees on its own.

A note the pass could not read at all is not a row but a `skipped` entry, and a note directory it could not list is one too — reported under the directory's own name, because "nothing to migrate" and "nothing I could see" are the same JSON otherwise, and this report is what an `--apply` is approved from.
The counts keep the two apart: `unreadable_dirs` counts the directories and `skipped` counts only the notes, so `counts.skipped` is deliberately smaller than the `skipped` array when a directory is in it.
One directory entry stands for however many notes went unseen, which is why folding it into a note count would put a fixed number where the true one is unknown.
A non-zero exit says a pass left work behind, not that a human must move a file: where the pass renamed the note that was in the way, a re-run clears the conflict on its own.
What no pass can clear is two notes each holding the name the other wants — that one is a human's to break.
A missing vault root is refused in both modes rather than reported as a vault with nothing to migrate, since the dry run is what an `--apply` is approved from.
The tool takes the vault lock for `--apply` and none for `--dry-run`, and it is a repair a human runs, not a routine phase.

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

`kboat-validate` checks every vault note against its schema and prints the violations as JSON: per-field (`missing_field`, `empty_required`, `not_bool` / `bad_enum` / `bad_date` / `not_list` / `not_int` / `not_str`), plus any cross-field rules the schema defines, `parse_error`, and two for a note the pass could not read at all: `icloud_placeholder` against the placeholder's own path, and `unreadable_dir` against a note directory the OS refused to list.
Both are violations like any other, so both enter `violations`, `counts.total` and `counts.by_code`; what they leave alone is `checked` and the stats, which keep their own meanings — `checked` counts the notes the pass could list and the stats the ones it could read, a note that would not parse being in the first and not the second.
What they add is that a vault read in part stops reporting as a clean one, which is the reading a short backlog otherwise invites.
It is read-only and report-only by default (exit 0; `--strict` exits non-zero), so a routine runs it last and surfaces the violations as drift for a human to fix.
`--stats` adds a block of backlog-health counts, defined by the owning member over its own lifecycle predicates rather than over the schema; K-Boat's set is in `kboat-notes` ([Backlog stats](../kboat-notes/references/validation.md#backlog-stats)).
Stats never affect the exit code — they describe how the backlog is moving, not whether a note is well-formed.

## The write contract

A note is never hand-assembled.
The write is owned by `kboat.write.upsert(schema, vault, record, *, today)` in the `kboat` library, with a CLI wrapper `kboat-note write --type <t>` that reads a `{slug, fields, body?}` JSON record on stdin.
K-Boat's prose skills pipe a record to the CLI; a Python member such as feed-filter calls `upsert` directly.
`kboat-repos write` is a second CLI over the same `upsert`, differing in the record shape it accepts (a `gather` record plus the judged fields, rather than `{slug, fields, body?}`), in that its `fields` block writes only the keys it owns — dropping one the schema does not declare, one belonging to the human or the schema (`reading`, the date stamps), and one the writer sets itself from the record's top level, and reporting all three as `dropped_fields` — and in that it authors no body of its own; everything else below holds for it too.

From a `{slug, fields, body?}` record, `upsert` guarantees:

- The note is written at `<type-dir>/<slug>.md` (the directory is `DIR_BY_TYPE[type]`) in the schema's canonical field order, YAML quoting and empty-value rendering handled.
  - The slug names one file inside that directory and nothing else, so a record whose slug is not a filename — blank, opening with a dot, or carrying edge whitespace, a control character, or one of the `/ \ : * ? " < > |` the naming section forbids — is refused and written nowhere.
    - Every slug a member writes is a URL hash, an ASIN, or a name it chose itself, so the rule costs a real one nothing and keeps an assembled record from reaching outside its type's folder.
  - A field name outside the property-key grammar — ASCII letters, digits and `_`, never opening with a digit, which `snake_case` already satisfies — is refused the same way, and the whole record with it.
    - A wrong *value* is kept as a quoted scalar and reported by `kboat-validate`; a name has no such fallback, since quoting it puts the property outside what the reader can decode and so beyond both the next write and the validator.
    - A property a human hand-added under a name of their own is a different matter — the write carries it back untouched (below).
- **Merge on update.** If the file is absent it is created; if present, the record's `fields` are merged over the existing note — provided keys win, absent keys are preserved — so a partial write (omitting a field, or `body`) preserves what it omits.
- **Always-present defaults on create.** A present field absent from the record is filled with its schema default (a boolean → `false`), so the Base-filter booleans are written on every note from creation.
  - On *update* a field the note has lost is left lost, not backfilled — a write re-renders only what it changes (below), and a writer that filled in blanks it was not asked about would be re-rendering the whole note.
  - Drift of that kind is `kboat-validate`'s to report and a human's to repair.
- **Date stamps.** A `created`-stamp field (e.g. `added_date`) is set to `today` on create and preserved after; a `refreshed`-stamp field is set on every write.
- **A value is never erased to fit its field.** A list field given something with no items to lay out keeps it as the scalar it is rather than writing `[]`, so a wrong type reaches `kboat-validate` as a `not_list` for a human to see instead of vanishing under a successful write.
  - That works for an inline list too, whose valid form is itself a string: what the check accepts is a string the writer would read back as a sequence, not any string at all.
    - A string that holds an inline sequence (`"[a, b]"` — what the reader hands back for one) is read into its items and written in the field's own style.
  - What the writer will not do is put a value into the note bare when doing so could break the frontmatter block: a value it cannot render as its field's type is quoted, so a wrong one costs its own field and never the whole note.
  - A null item inside a list is written as an explicit empty item, in either style.
    - It is the one value with no shape of its own to keep — nothing is lost by writing the empty string it reads back as, and a bare `-` (or the word `None` inline) would be a value the record never carried.
- **What the writer emits, it reads back as itself — and so does every other reader.** Re-writing a note the writer already wrote changes nothing but a refresh stamp, down to a value the field could not hold: kept as a quoted scalar, that scalar is what the next write is handed and what it emits again.
  - A rendering that read back as something else would move the note once per unattended run with no edit behind it, and the drift would look like the vault's own.
  - The other readers are the point of the second half.
    - A value stays inside its own property for Obsidian and for any YAML reader too, which is why a character that one of them would treat as the end of a line is escaped rather than written raw.
    - Source titles, summaries, and repo descriptions are page text and model output; without that rule the tail of one arrives as a property of the note — including `url`, the identity the collision check is there to defend.
- **Slug verification.** For a URL-named schema, the record's slug is recomputed from the record's own `url` and a mismatch is refused as `{status: "slug_mismatch", identity, url, expected, got}`, written nowhere.
  - The name and the identity are one fact, so the writer settles it rather than trusting what it was handed; a note filed anywhere else would be a second identity for the same page, and nothing later could tell that from a genuinely different page.
  - It is checked before the file is even located, since a wrong slug names the wrong file and the collision check below would then run against a note the record was never about.
  - A record that carries no `url` — a later write filling in a summary, or an upload source that has none — makes no claim to check and passes.
- **Collision check.** When the schema declares an `identity` field (e.g. `url`), an existing note at the same slug that cannot be shown to be the same note is a collision — returned as `{status: "collision", reason, …}` and written nowhere.
  This is the de-dup-by-identity rule the naming section relies on.
  Two identity URLs are compared the way the slug is made, by their canonical forms: a page reached by a second link lands on the note it already has, so a verbatim comparison would report that as a hash clash and refuse an update that is the same page.
  A stored URL no parser can take is compared exactly instead, which fails closed.
  Two reasons, because the check exists to refuse and so has to fail closed either way:

  - `identity_differs` — the note's identity value is plainly a different one.
  - `unreadable_identity` — the record names an identity but the note holds its own in a shape the reader cannot decode, so nothing can be compared.
    - Repairing the note by hand is the only way forward; the writer will not guess.
  - The identity a note was created with is the one it keeps: where the record names the same page by another link, the stored value is preserved rather than overwritten.
    - It is the note's provenance, and for a normally-fetched web source the string the NotebookLM source id is resolved by matching, so a second link's spelling must not replace it.
- **Body.** The body *mode* is a fixed schema attribute (`NoteSchema.body`), not a record field: for a `verbatim` schema the record's `body` content is appended after the frontmatter, `notes` wraps it in a `## Notes` section, and `none` means the writer never authors one.
  - An `upsert` always preserves the body already in the note, under every mode — `none` says K-Boat writes no body of its own, not that it may delete one a human added below the fence, and `notes` owns its `## Notes` section rather than the whole body.
- **Only what changes is re-rendered.** Every frontmatter entry the record does not write is put back exactly as the note held it, including one the reader can represent only approximately (an inline list, a quoted string) and one it cannot represent at all (a hyphenated or quoted key, a nested mapping, a block scalar).
  - A note is rewritten *around* what the write is not about, so a property outside the schema — Obsidian's own, or a plugin's — survives a routine that rewrites the note daily.
  - Every schema field keeps its canonical position, written or carried; anything else follows in the order the note already had.
  - The exception is a line that owns no key of its own and would attach to the line above it — that goes first, where there is nothing for it to attach to, since anywhere else would hand it to a key that never had it.
  - An unrepresentable property is invisible to `kboat-validate`, which reads what the scanner models.
    - If it collides with a schema field, expect a `missing_field` for that field: the note holds it in a shape the checks cannot read, which is drift worth seeing.
  - "Does not write" is per line, so a comment sharing a line with a field the record writes goes with it.
    - Put a note to self on its own line, where it is an entry of its own and survives.

The merge rule gives a member a clean **resurrection** idiom: to re-surface a note it force-writes the fields it owns (e.g. a status boolean back to `false`) while omitting the fields a human owns, so the human's values survive the update.

**A name an iCloud placeholder holds is taken, not free.**
The vault is iCloud-synced, so an evicted file is not gone: it is a `.<name>.icloud` placeholder beside where the file was, and `Path.exists()` answers `False` for it exactly as it does for a name nothing occupies.
An evicted note matches no `*.md` glob either, so a folder scan reads a half-synced vault as a complete one.
So this is a rule to hold a writer to, not a description of what they all do: whatever decides anything from a file's absence — whether a rename's target is free, whether a note is new rather than one to merge into, whether a source's PDF is there — asks the placeholder question of both names first, or it writes over an identity another file still holds and reports success.
What follows is not a conflict anyone sees: iCloud settles the two later by suffixing or dropping one, so the duplicate arrives quietly and long after the run that made it.
`kboat.io_utils` owns the recipe, and it is four questions rather than one — copy them rather than a call site, which may be older than the rule.

- `name_taken(path)` — is this name spoken for at all.
- `file_present(path)` — is a **file** there, which is what says *by what* a taken name is held.
  - This is where the swallow used to live: `exists()` answers "no file" for a link into an unreadable tree, so a caller reported a name nothing will free and sent a human after a broken symlink that was not there.
- `name_occupied(path)` — is anything at **that name itself**, which is `name_taken`'s first half and what separates the two ways a non-file holds a name.
- `list_note_dir(directory)` — one folder's notes and the placeholders shadowing them.

A caller classifying a taken name asks the middle two in that order, and never reaches "evicted" by elimination.
`name_taken` ORs the name and the placeholder beside it, so once `file_present` says no, testing only the placeholder lets a stale stub answer for a directory or a dangling symlink at the name — reporting a name no download will ever free as one that is merely waiting on iCloud, which is the one answer both skills route to nobody.

Those four **raise** where the vault refuses the read, so every caller owes a boundary — per item for the first three, per directory for `list_note_dir` — and decides the *cause* inside it: a refusal named as anything but a refusal is the defect they exist to prevent.
`stranded_stub(path)` is the exception and the only one: it is asked after the decision to move, about a name the writer is giving up, so it **reports** rather than refuses.
It answers found, none, or could-not-tell, and a caller passes that third answer on.
Refusing there would abort a rename over a stub — and the probe fails on exactly the long names this repair exists for, since a filename of 248 bytes or more makes its `.icloud` sibling exceed the limit.
The two are not interchangeable: a scan needs `list_note_dir` whether or not it also claims a name, since `name_taken` answers about a name it was already given and an evicted note is one nothing handed it.
Which names a writer has to ask about depends on where each came from: a name a `list_note_dir` listing produced is already answered, and one a record or a slug formula produced is not.
So a rename driven by a scan asks about its target, while one that derives both names itself asks about both, since either being evicted is a reason not to move.
That is what `pathlib` will not do for them: from CPython 3.14 `Path.exists` swallows every `OSError`, and `Path.glob` swallows the one `os.scandir` raises on an unlistable directory in every version, so a permission-denied probe comes back as an invitation to write there and an unreadable folder as an empty, clean one.
**A `created` status is not a claim that the name was free.**
`upsert`'s create-versus-merge decision is a bare `exists()`, so at an evicted slug it takes the note for a new one: the merge and the collision check above are both skipped and the note is rewritten from what the record alone carries.
Read it as "the writer found no file there" and nothing further.
The `kboat-doctor` placeholder scan is a precondition and not a substitute: it runs once, before the phases, and an eviction can land on a vault it passed.

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
**Do not delete `<vault>/.kboat.lock`.**
It persists between holds — carrying the last holder's record, which is why it looks like leftover state and is not — because exclusion is an agreement about one inode: remove it mid-run and the next writer creates a different inode and locks that instead, leaving two runs writing at once.
Nothing ever needs it cleared, which is the difference from a design with a stale window a human has to break.
What buys that is a premise worth stating, because it is the one thing that would invalidate the design: **all contention is same-host on a local volume.**
`~/Library/Mobile Documents/…` is not a network mount but a local APFS directory with a file-provider sync extension, so `flock` there is ordinary APFS advisory locking — verified against this vault, including that a holder killed without releasing leaves the lock free.
A vault on a genuine network filesystem would need that re-checked, since `flock` over NFS or SMB is where the semantics stop holding.

- **A read-only command takes no lock**, so a query neither blocks nor is blocked — `kboat-lifecycle --dry-run`, `kboat-repos refresh --dry-run`, `kboat-note migrate-slugs --dry-run`, `kboat-pick candidates`, `kboat-queue list`, `kboat-validate`, and `kboat-recall`'s search all read a vault another run is writing.
  - **`kboat-doctor` takes none either**, though its writability probe does write.
    - It creates and removes one uniquely-named file of its own and touches no note, and it runs to find out whether the vault can be written at all.
    - Holding the lock first would be circular, and a pre-flight check that refused whenever a run was in progress would be useless exactly when it is wanted.
- **One policy: wait a little, then refuse.** Every writer takes the lock on the same terms — a few seconds' wait, then a refusal.
  - There is no attended/unattended split, because the `kboat-*` CLIs are run both ways: by a person at a keyboard and by the scheduled routine.
  - A person does not perceive the wait, and an expired one hands back the same holder record an immediate refusal would have.
  - What rules out refusing immediately is that holds are not all short.
    - `kboat-repos refresh` keeps the lock across the `gh` fetch of every note in the catalogue, because its read, fetch and rewrite are one read-modify-write — so "just retry" is not advice a refused caller can act on, and a few seconds of patience covers the ordinary case of overlapping a single note write.
- **A refused caller reports and ends that step.** It prints `{"status": "locked", "holder": {pid, started, path}}` on stdout **in place of** its usual output, names the holding process on stderr, and exits non-zero without writing anything.
  - Every `holder` key is always present and any may be null: the record is written just after the lock is taken, so a refusal landing in that window reads the previous holder's, and a crash mid-write leaves one that does not parse at all.
  - A caller reads that record instead of the keys it came for, so it reports the refusal and ends that step rather than parsing on — that step, not a routine around it, whose later phases make their own attempts.
  - A refusal belongs in the run summary rather than a notification, since the next run recovers on its own.
  - For feed-filter the entry is simply not written, and its never-lost contract carries it: nothing is recorded seen, so the next gather rediscovers it.
    - That is why the wait matters more to it than to a K-Boat phase, whose work survives being deferred — the dispositions, the cooldown clock and the queue are all still on disk and every phase is idempotent.
- **A lock that cannot be taken at all is not a refusal.** A missing vault root, a denied iCloud tree, a filesystem that will not take an `flock`, or — only on the run that first creates the lock file — a vault root that cannot be written to, is reported on stderr with **no** `locked` record and an **empty stdout**, because there is no holder and nothing to come back for.
  - The report-shaped CLIs (`kboat-lifecycle`, `kboat-pick set`, `kboat-repos refresh`, `kboat-note migrate-slugs --apply`) name it `vault lock unavailable: …`; the note writers fold it into their `write failed: …`, and feed-filter into its `error: …`.
    - What is common to all of them is the shape, not the wording.
  - Do not parse stdout, and do not retry: unlike a refusal this does not clear itself, so report it as needing a human and stop.
  - `kboat-doctor` diagnoses two of its causes and not the rest: its `vault_root` and `vault_writable` checks cover a missing or unwritable root, but nothing there inspects the lock file, so a `.kboat.lock` that is a directory, *any* symlink (the open is `O_NOFOLLOW`, so a live one fails as surely as a dangling one), or one on a filesystem refusing `flock` leaves doctor reporting `ok` while every write fails.
    - Read the stderr line rather than assuming a green doctor means the lock is fine.
  - How an unattended run raises it belongs to the scheduled-task prompt, not here.

**What is outside.**
The unit these mechanics protect is one tool invocation writing one file's contents, and five things sit outside it.

- **A file an agent writes itself**, rather than through a `kboat` tool: the distillation review report in `Reviews/`, a rescued source's PDF in `PDFs/`, and the deletion of a drained `Queue/` capture.
  - No tool holds the lock on their behalf, and in the routine each belongs to one phase of one run.
- **A change that spans two files.** Each write is atomic on its own; a pair of them is not.
  - `kboat-repos refresh` adopting a rename writes the new note and then unlinks the old one, so a crash in between leaves both — reported by the next run's `kboat-validate` as two notes for one repo, for a human to merge.
  - `kboat-note migrate-slugs --apply` moves a source's PDF and then the note, ordered so that a crash between them leaves the note where the next scan looks for it, which is what lets a re-run finish the pair rather than lose track of the half that moved.
- **Anything attached to a note's inode rather than its contents.** The atomic write renames a new file over the old one, so a rewrite preserves the note's permissions and nothing else: extended attributes, ACLs, a per-file `com.apple.macl` grant and the creation date all belong to the replaced inode, and a Finder tag on a note the routine rewrites daily will not last.
  - A stray `<name>.md.<random>.tmp` is the same story from the other side — the cleanup runs on an exception, not on a `SIGKILL`, and no scanner globs it because they all read `*.md`.
- **A note saved in Obsidian while a run is writing.** Obsidian holds no lock and writes its own way, and `kboat-lifecycle` and `kboat-pick set` rewrite a whole note, so a hand-edit saved in the same moment loses one side or the other.
  - The remedy is not a mechanism: do not hand-edit notes during a run, and the daily routine runs while nobody is at the keyboard.
- **The judgement an agent forms between its own read and the write it then asks for.** An agent reads a source note, decides a `summary` or a disposition from it, and calls `kboat-note write` some time later; the writer re-reads the note under the lock, so the body, the human's `reading` checkbox, and every field the record omits survive — but a field the record *does* carry overwrites whatever changed in that gap.
  - It is the one lost update the lock does not close, narrowed to the fields one write names.

Closing the agent-level ones would mean exposing acquire and release as a tool of their own, for an agent to hold around a whole phase.

## Base-authoring discipline

A [Base](https://help.obsidian.md/bases) is a saved view over the vault's notes; how its filters are written decides whether it stays complete.

- **Filter only on always-present values.** Every filter must be a plain boolean, or an `==` / `!=` over an always-present property — never a `!=` over a property that might be missing, and never a date-emptiness test.
- **Why.** Obsidian Bases excludes a note missing the property from a `!=` filter, so a `!=` over a sometimes-missing property silently drops those notes.
  - A view stays complete only because the filter booleans are written on every note at creation (the write contract's always-present defaults) — the create-time invariant, not booleanness alone, is what guarantees it.
- **Signal a date state with a column, not a filter.** To tell a date-set state from a date-empty one (e.g. "distilled" vs "still ripe"), carry the date as a column the human reads rather than filtering on its emptiness.
- **Show the title through a formula for hash- or ASIN-named notes.** The filename is an opaque hash or ASIN, so render the readable title with a formula (`title_link`, or whatever the view's lead column is called) rather than the filename.
  - A date-named note needs no formula: `file.name` is already legible.
  - Where that formula links is the Base's own call.
    - `file.asLink(note.title)` shows `title` as text but opens the hash-named file — the default, and the right one whenever the note itself is worth opening: it has a body, or fields the reader wants in full.
  - Point somewhere else when nothing behind the row is worth opening: a body-less note whose hidden fields are only provenance is better served by `link(url, …)` onto the page itself.
- **First view is the default.** A Base shows [its first view on open](https://help.obsidian.md/bases/views), so order the day-to-day working view first.
- Column widths and other cosmetics are per-vault tweaks, not part of the authored Base.
