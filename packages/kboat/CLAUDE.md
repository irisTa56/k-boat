# CLAUDE.md

The `kboat` package — K-Boat's deterministic mechanical core (the library that the K-Boat skills call).
This file covers only what is specific to working on the library; the K-Boat product architecture and the shared workspace conventions are in the [root CLAUDE.md](../../CLAUDE.md).

## What this package is

The library surface — its seven console scripts and six shared modules (`kboat.frontmatter`, `kboat.schema`, `kboat.write`, `kboat.io_utils`, `kboat.lock`, `kboat.cli`) — is described in [README.md](README.md).
Its **spec** is split (both at the repo-root `.claude/skills/`): the shared vault contract — naming, the schema/validate/write contract, durability and the vault lock, Base discipline — is the `kboat-vault-conventions` skill, and K-Boat's note-type field semantics and lifecycle are the `kboat-notes` skill.

## Working on it

- Change the spec first. Edit the owning spec — `kboat-vault-conventions` for a shared convention, `kboat-notes` for a K-Boat note type or lifecycle — then the code (`src/kboat/`) and its tests (`tests/`), then reconcile the schema tables (the `test_doc_schema_sync` gate checks them against `kboat.schema`).
- `kboat.lock` and `kboat.io_utils` are the vault's concurrency and durability floor. Take the lock at a CLI edge, never inside a writer (an `flock` is per open file description, so a nested acquisition waits out its own hold), and never add a second file-writing path beside `atomic_write_text`.
- Zero runtime dependencies by design, so the core stays a pure, independently-testable package. Do not add a runtime dependency.
- Dev/QA conventions (ruff, `ty`, pytest, the coverage gate) are workspace-wide — see the [root CLAUDE.md](../../CLAUDE.md). Run this member's gate with `mise run qa:py:kboat`.
