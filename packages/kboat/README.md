# kboat — K-Boat's deterministic mechanical core

The tested Python that the [K-Boat](../../README.md) skills call, so the model neither re-derives the mechanical logic nor pays tokens for it.
Its **spec** is split by ownership: the shared vault contract (naming, the schema/validate/write contract, durability and the vault lock, Base discipline) is the `kboat-vault-conventions` skill, and K-Boat's note-type field semantics are the `kboat-notes` skill. Change the relevant spec first, then this package and its tests.

## Console scripts

- `kboat-lifecycle` — the distillation lifecycle state machine: boolean/date predicates over frontmatter, no judgement.
- `kboat-repos` — the repo catalogue's mechanics: `gather` and `refresh` over the `gh` CLI, and `write` over the shared writer.
- `kboat-pick` — the daily-pick mechanics (`candidates`/`set`), no LLM and no NotebookLM.
- `kboat-validate` — checks every vault note against `kboat.schema` and prints violations as JSON; `--stats` adds the backlog-health counts.
- `kboat-doctor` — checks the vault's environment preconditions (root, writability, folders, the questions file, directory readability, iCloud placeholders) before a run.
- `kboat-note` — `write` (create-or-update one note from a `{slug, fields, body?}` JSON record), `slug` (the slug oracle for one URL), and `migrate-slugs` (rename the vault's URL-named notes to the slugs their URLs name).
- `kboat-bookmarklet` — print the queue-capture bookmarklet (Obsidian URI) to paste into a browser bookmark.
- `kboat-queue` — parse the vault's `Queue/` captures into `{path, url, title}` JSON for `kboat-ingest` to drain.
- `kboat-concept` — `shape`, the reading-group classifier: reads a concept note on stdin and answers whether its `## Observations` carries any `###` group at all, which is the branch `kboat-distill` takes before adding to one; text carrying no such heading is refused (exit 2, empty stdout) rather than answered.

## Shared modules

- `kboat.frontmatter` — read, scoped rewrite, YAML-safe rendering. Reading is a focused scanner rather than a YAML parser, so `parse_entries` hands back each entry's verbatim source alongside its value, for a caller that has to write the note back whole.
- `kboat.schema` — the code-authoritative mechanical schema (field names, order, kinds, defaults, the always-present booleans, the enums). `kboat-vault-conventions` describes the schema/validate/write contract around it, and `kboat-notes` keeps the K-Boat field semantics; both point here.
- `kboat.canonical` — URL canonicalization (`canonical_url`, the `CanonicalUrl` NewType, `resolve_link`). One page reached by two links normalizes to one string, which is what both the note slug below and feed-filter's seen-store key on — so a note and the store that gates it cannot disagree about what one page is.
- `kboat.naming` — `note_slug`, the single slug oracle: the URL-hash of a note's canonical `url`, shared by every URL-named type and exposed to the skills as `kboat-note slug`.
- `kboat.write` — schema-driven note assembly and create-or-update (`build_note`/`render_field`/`upsert`), the one writer all note types share. An update re-renders only what it changes, so the existing body and any frontmatter the write is not about survive it untouched, and a record whose slug is not the one its `url` names is refused rather than written.
- `kboat.io_utils` — `atomic_write_text`, the single writer for every durable file these tools write (feed-filter's `sites.toml` too): temp file in the same directory, `fsync`, `os.replace`, then an `fsync` of the parent directory, so a write is both atomic and durable past a power loss. A raise means nothing was written. It also answers what is there — `name_taken` (a file, an iCloud placeholder, or a symlink all take a name), `file_present` (a file, and so what a taken name is held *by*), `name_occupied` (anything at that name itself, which is what separates a stale stub from a non-file holding the name), and `list_note_dir` (one directory's notes and the placeholders a `*.md` scan would walk past). All four raise where the vault refuses the read rather than answering "nothing there", which is what `pathlib` would have said.
- `kboat.lock` — `vault_lock`, the vault-wide mutual exclusion every mutating run holds so two runs cannot interleave over one vault. An advisory `flock`, so a crashed holder's lock is released by the kernel and there is no stale state to recover; a held vault is waited on for a few seconds and then refused with a record naming the holder.
- `kboat.cli` — the plumbing the console scripts share: the `--vault` and `--today` flags every vault CLI takes (feed-filter's note-writing subcommands too, so a date reaching the writer has been validated the same way whatever CLI it arrived at), plus — for the two note writers — the stdin record read and the mapping from outcome to exit code, so two contracts over one writer cannot drift apart.

## Development

- Zero runtime dependencies by design, so the core stays a pure, independently-testable package.
- QA: `mise run qa:py:kboat` (ruff, `ty`, pytest, plus a per-file coverage floor); autofix with `mise run fmt:py:kboat`. The workspace-wide gates and layout are in the [root README](../../README.md) and [CLAUDE.md](../../CLAUDE.md).
