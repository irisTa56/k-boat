# CLAUDE.md

The `kboat` package — K-Boat's deterministic mechanical core (the library that the K-Boat skills call).
This file covers only what is specific to working on the library; the K-Boat product architecture and the shared workspace conventions are in the [root CLAUDE.md](../../CLAUDE.md).

## What this package is

The library surface — its nine console scripts and eight shared modules (`kboat.frontmatter`, `kboat.schema`, `kboat.canonical`, `kboat.naming`, `kboat.write`, `kboat.io_utils`, `kboat.lock`, `kboat.cli`) — is described in [README.md](README.md).
Its **spec** is split (both at the repo-root `.claude/skills/`): the shared vault contract — naming, the schema/validate/write contract, durability and the vault lock, Base discipline — is the `kboat-vault-conventions` skill, and K-Boat's note-type field semantics and lifecycle are the `kboat-notes` skill.

## Working on it

- Change the spec first. Edit the owning spec — `kboat-vault-conventions` for a shared convention, `kboat-notes` for a K-Boat note type or lifecycle — then the code (`src/kboat/`) and its tests (`tests/`), then reconcile the schema tables (the `test_doc_schema_sync` gate checks them against `kboat.schema`).
- The JSON a console script prints on stdout — whatever its own docstring calls it — is read by prose an agent executes, and reconciling every site that restates or branches on it is the root [CLAUDE.md](../../CLAUDE.md)'s "Keep this file current" duty, which reaches outside this repository where it is owed a confirm-back rather than an edit.
  - So the budget is its keys plus the values any closed set among them carries, whether or not anything branches on a given one: decide that prose's branch from as few as it actually needs, since the lines grow out of them and not the other way round.
  - Little of that reconciliation is checked, and only the part inside this repository can be — where both sides are structured, a table on one and a field list or a `StrEnum` on the other, which is what `test_doc_schema_sync` pins and the pattern to reach for.
- `kboat.lock` and `kboat.io_utils` are the vault's concurrency and durability floor. Take the lock at a CLI edge, never inside a writer (an `flock` is per open file description, so a nested acquisition waits out its own hold), and never add a second file-writing path beside `atomic_write_text`.
- `kboat.io_utils` also owns whether a name is free, which on this iCloud-synced vault is not what `Path.exists()` or a glob answers. Ask the `kboat.io_utils` probes rather than `pathlib`; they raise rather than guessing, so a caller owes a boundary. Which to ask where, and at what granularity, is `kboat-vault-conventions` "The write contract".
- Zero runtime dependencies by design, so the core stays a pure, independently-testable package. Do not add a runtime dependency.
- Dev/QA conventions (ruff, `ty`, pytest, the coverage gate) are workspace-wide — see the [root CLAUDE.md](../../CLAUDE.md). Run this member's gate with `mise run qa:py:kboat`.
