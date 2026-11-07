# The write contract in full

What `kboat.write.upsert` guarantees, and the two rules for asking the vault about a name and reading notes from it.
Who owns the write, and the CLIs over it, are in [The write contract](../SKILL.md#the-write-contract).

## What `upsert` guarantees

From a `{slug, fields, body?}` record, `upsert` guarantees:

- The note is written at `<type-dir>/<slug>.md` (the directory is `DIR_BY_TYPE[type]`) in the schema's canonical field order, YAML quoting and empty-value rendering handled.
  - The slug names one file inside that directory and nothing else, so a record whose slug is not a filename — blank, opening with a dot, or carrying edge whitespace, a control character, or one of the `/ \ : * ? " < > |` [the naming section](../SKILL.md#naming) forbids — is refused and written nowhere.
    - Every slug a member writes is a URL hash, an ASIN, or a name it chose itself, so the rule costs a real one nothing and keeps an assembled record from reaching outside its type's folder.
  - A field name outside the property-key grammar — ASCII letters, digits and `_`, never opening with a digit, which `snake_case` already satisfies — is refused the same way, and the whole record with it.
    - A wrong *value* is kept as a quoted scalar and reported by `kboat-validate`; a name has no such fallback, since quoting it puts the property outside what the reader can decode and so beyond both the next write and the validator.
    - A property a human hand-added under a name of their own is a different matter — the write carries it back untouched (below).
- **What holds the slug decides create versus merge**, asked the way "A name is asked about through `kboat.io_utils`" below says and in its order, never by a bare `exists()`.
  - A file there is merged into (the next bullet), and a slug nothing holds is created.
  - A slug held by something that is not a file — a directory, or a symlink that leads nowhere — raises an `OSError` and writes nothing, which `kboat-note write` and `kboat-repos write` report as `write failed: …` and feed-filter as `error: …`, each with exit 1.
    - No run frees that name, so it is a human's.
  - A probe the vault refuses raises the same way, so a refusal is never reported as a free name.
- **A note naming a key twice is refused.** An existing note that names any key on more than one top-level line — counted as `kboat-validate`'s `repeated_key` counts it — is refused as `{status: "repeated_key", slug, path, keys}`, written nowhere, `keys` listing every such key.
  - The write re-assembles the note with one line per key, so it would delete a line a human wrote, whether or not the record touches that key, and which line they meant is not in the note.
  - A key held only on one line the reader cannot decode is not refused: a write setting it replaces that line, and one that does not carries it back as it is.
  - No run clears it; the repair is `repeated_key`'s ([Schema authority and validation](../SKILL.md#schema-authority-and-validation)), and a caller reports the note and carries on with the rest.
- **Merge on update.** If a file is there, the record's `fields` are merged over the existing note — provided keys win, absent keys are preserved — so a partial write (omitting a field, or `body`) preserves what it omits.
  - Omitting a key therefore never clears it; to clear a field, give it as `null`, which writes the field's empty form: a bare `key:` for a string or a date, `[]` for a list.
- **A derived field is the writer's.** A field `kboat.schema` declares as derived from another (a source note's `gemini_url` and `notebooklm_url`, from `notebooklm_id`) follows that field: a record setting it to a value derives the field, and one setting it to `null` or a blank string clears the field.
  - A record that does not name the field it derives from leaves the derived field as it is, as the merge above leaves anything omitted.
  - A record supplying a derived field itself, even as `null`, is refused and written nowhere, as is one giving the field it derives from a value that is neither a string nor `null`: both are records to fix, with the refusal on stderr and exit 2.
- **Always-present defaults on create.** A present field absent from the record is filled with its schema default (a boolean → `false`), so the Base-filter booleans are written on every note from creation.
  - On *update* a field the note has lost is left lost, not backfilled — a write re-renders only what it changes (below), and a writer that filled in blanks it was not asked about would be re-rendering the whole note.
  - Drift of that kind is `kboat-validate`'s to report and a human's to repair.
- **Date stamps.** A `created`-stamp field (e.g. `added_date`) is set to `today` on create and preserved after; a `refreshed`-stamp field is set on every write.
- **A value is never erased to fit its field.**
  - A list field given something with no items to lay out keeps it as the scalar it is rather than writing `[]`, so a wrong type reaches `kboat-validate` as a `not_list` for a human to see instead of vanishing under a successful write.
  - That works for an inline list too, whose valid form is itself a string: what the check accepts is a string the writer would read back as a sequence, not any string at all.
    - A string that holds an inline sequence (`"[a, b]"` — what the reader hands back for one) is read into its items and written in the field's own style.
  - What the writer will not do is put a value into the note bare when doing so could break the frontmatter block: a value it cannot render as its field's type is quoted, so a wrong one costs its own field and never the whole note.
  - A null item inside a list is written as an explicit empty item, in either style.
    - It is the one value with no shape of its own to keep — nothing is lost by writing the empty string it reads back as, and a bare `-` (or the word `None` inline) would be a value the record never carried.
- **What the writer emits, it reads back as itself — and so does every other reader.**
  - Re-writing a note the writer already wrote changes nothing but a refresh stamp, down to a value the field could not hold: kept as a quoted scalar, that scalar is what the next write is handed and what it emits again.
  - A rendering that read back as something else would move the note once per unattended run with no edit behind it, and the drift would look like the vault's own.
  - The other readers are the point of the second half.
    - A value stays inside its own property for Obsidian and for any YAML reader too, which is why a character that one of them would treat as the end of a line is escaped rather than written raw.
    - Source titles, summaries, and repo descriptions are page text and model output; without that rule the tail of one arrives as a property of the note — including `url`, the identity the collision check is there to defend.
- **Slug verification.** For a URL-named schema, the record's slug is recomputed from the record's own `url` and a mismatch is refused as `{status: "slug_mismatch", identity, url, expected, got}`, written nowhere.
  - The name and the identity are one fact, so the writer settles it rather than trusting what it was handed; a note filed anywhere else would be a second identity for the same page, and nothing later could tell that from a genuinely different page.
  - It is checked before the file is even located, since a wrong slug names the wrong file and the collision check below would then run against a note the record was never about.
  - A record that carries no `url` — a later write filling in a summary — makes no claim to check and passes.
- **Collision check.** When the schema declares an `identity` field (e.g. `url`), an existing note at the same slug that cannot be shown to be the same note is a collision — returned as `{status: "collision", reason, …}` and written nowhere.
  - This is the de-dup-by-identity rule [the naming section](../SKILL.md#naming) relies on.
  - Two identity URLs are compared the way the slug is made, by their canonical forms: a page reached by a second link lands on the note it already has, so a verbatim comparison would report that as a hash clash and refuse an update that is the same page.
  - A stored URL no parser can take is compared exactly instead, which fails closed.
  - Two reasons, because the check exists to refuse and so has to fail closed either way:
    - `identity_differs` — the note's identity value is plainly a different one.
    - `unreadable_identity` — the record names an identity but the note holds its own in a shape the reader cannot decode, so nothing can be compared.
      - Repairing the note by hand is the only way forward; the writer will not guess.
  - The identity a note was created with is the one it keeps: where the record names the same page by another link, the stored value is preserved rather than overwritten.
    - It is the note's provenance, and for a normally-fetched web source the string the NotebookLM source id is resolved by matching, so a second link's spelling must not replace it.
- **Body.** The body *mode* is a fixed schema attribute (`NoteSchema.body`), not a record field: for a `verbatim` schema the record's `body` content is appended after the frontmatter, `notes` wraps it in a `## Notes` section, and `none` means the writer never authors one.
  - An `upsert` always preserves the body already in the note, under every mode — `none` says K-Boat writes no body of its own, not that it may delete one a human added below the fence, and `notes` owns its `## Notes` section rather than the whole body.
- **Only what changes is re-rendered.**
  - Every frontmatter entry the record does not write is put back exactly as the note held it, including one the reader can represent only approximately (an inline list, a quoted string) and one it cannot represent at all (a hyphenated or quoted key, a nested mapping, a block scalar).
  - A note is rewritten *around* what the write is not about, so a property outside the schema — Obsidian's own, or a plugin's — survives a routine that rewrites the note daily.
  - Every schema field keeps its canonical position, written or carried; anything else follows in the order the note already had.
  - The exception is a line that owns no key of its own and would attach to the line above it — that goes first, where there is nothing for it to attach to, since anywhere else would hand it to a key that never had it.
  - An unrepresentable property is invisible to `kboat-validate`, which reads what the scanner models.
    - If it collides with a schema field, expect a `missing_field` for that field: the note holds it in a shape the checks cannot read, which is drift worth seeing.
  - "Does not write" is per line, so a comment sharing a line with a field the record writes goes with it.
    - Put a note to self on its own line, where it is an entry of its own and survives.

The merge rule gives a member a clean **resurrection** idiom: to re-surface a note it force-writes the fields it owns (e.g. a status boolean back to `false`) while omitting the fields a human owns, so the human's values survive the update.

## A name is asked about through `kboat.io_utils`

**A name is asked about through `kboat.io_utils`, never answered by `pathlib`.**
Whatever decides anything from a file's absence — whether a rename's target is free, whether a note is new rather than one to merge into, whether a source's PDF is there — asks there, or it can write over a name something still holds and report success.
`pathlib` answers wrongly in two ways: from CPython 3.14 `Path.exists` swallows every `OSError`, and `Path.glob` swallows the one `os.scandir` raises on an unlistable directory in every version, so a permission-denied probe comes back as an invitation to write there and an unreadable folder as an empty, clean one.
And `exists()` follows symlinks, so a dangling one reads as a free name, and a writer told so replaces the link rather than the file it points at.
`kboat.io_utils` owns the recipe, and it is three questions rather than one — copy them rather than a call site, which may be older than the rule.

- `name_occupied(path)` — is anything at that name, a dangling symlink included; the question to ask before claiming it.
- `file_present(path)` — is a **file** there, which is what says *by what* a taken name is held.
  - `exists()` answers "no file" for a link into an unreadable tree, so a caller asking it reports a name nothing will free and sends a human after a broken symlink that is not there.
- `list_note_dir(directory)` — one folder's notes.

A caller classifying a taken name asks `file_present` first: a file is a note to merge with or a pair to report, and anything else at the name is one no run frees.
All three **raise** where the vault refuses the read, so every caller owes a boundary — per item for the first two, per directory for `list_note_dir` — and decides the *cause* inside it: a refusal named as anything but a refusal is the defect they exist to prevent.
An evicted file needs none of this: it keeps its name ([the eviction check](preconditions.md#the-checks)), so every probe answers for it as for a file that is here.
`upsert` holds itself to this rule for its create-versus-merge decision, so a `created` status says nothing held the slug.

## Reading notes: `kboat-note list`

**A skill step that reads frontmatter from a note folder, or from one note by slug, does it through `kboat-note list`, never a glob, `ls`, or a direct read.**
A glob reads a folder the OS refused to list as empty, and a direct read leaves each step to tell a refusal from an absence by itself; the command reports each of those instead of answering around it.
`kboat-note list --type <type>` prints `{notes, anomalies, counts}`, each note a `{slug, path, frontmatter}` from the folder `DIR_BY_TYPE` names, read through `list_note_dir`.
Its `anomalies` are `{path, error}` entries: one per note that could not be read or parsed, one per field a listed note holds in a shape the frontmatter reader does not model (the note is listed without it) or names on more than one line (the note is listed with the last), which are the fields `kboat-validate` reports as `missing_field` and `repeated_key`, and one under the folder's own name where the folder is absent, not a directory, or refused, which also exits 1 ([Vault preconditions](../SKILL.md#vault-preconditions)).

- `--slug <slug>` answers that one name as the writer resolves it, as "A name is asked about through `kboat.io_utils`" gives: a note, an anomaly for whatever else holds the name, or neither where the name is free.
  - Where no listed name is exactly `<slug>.md`, it asks the volume through `name_occupied`, so on one that folds case, as APFS does by default, the note it names is the one the write would merge into.
- `--field <name>` (repeatable) cuts each note's `frontmatter`, and its unreadable-field entries, to those fields.
- `--flagged <name>` (repeatable) keeps only the notes whose boolean field is `true`, and a note it drops reports an unreadable field only where that is the flag.
- Neither drops a note that could not be read, since nothing shows it to fail them.

The step names every anomaly beside its answer.
Where the folder could not be read, it says so in place of the answer, because the set is then empty and an empty set is what a folder with nothing in it looks like.
