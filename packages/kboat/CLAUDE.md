# CLAUDE.md

The `kboat` package — K-Boat's deterministic mechanical core (the library that the K-Boat skills call).
This file covers only what is specific to working on the library; the K-Boat product architecture and the shared workspace conventions are in the [root CLAUDE.md](../../CLAUDE.md).

## What this package is

The library surface — its nine console scripts and eight shared modules (`kboat.frontmatter`, `kboat.schema`, `kboat.canonical`, `kboat.naming`, `kboat.write`, `kboat.io_utils`, `kboat.lock`, `kboat.cli`) — is described in [README.md](README.md).
Its **spec** is split (both at the repo-root `.claude/skills/`): the shared vault contract — naming, the schema/validate/write contract, durability and the vault lock, Base discipline — is the `kboat-vault-conventions` skill, and K-Boat's note-type field semantics and lifecycle are the `kboat-notes` skill.

## Working on it

- Change the spec first, then the code (`src/kboat/`) and its tests (`tests/`), then reconcile the schema tables.
  - The owning spec is `kboat-vault-conventions` for a shared convention, `kboat-notes` for a K-Boat note type or lifecycle.
  - The `test_doc_schema_sync` gate checks those tables against `kboat.schema`.
- `kboat.lock` and `kboat.io_utils` are the vault's concurrency and durability floor.
  - Take the lock at a CLI edge, never inside a writer: an `flock` is per open file description, so a nested acquisition waits out its own hold.
  - Never add a second file-writing path beside `atomic_write_text`.
- `kboat.io_utils` also owns whether a name is free, which on this iCloud-synced vault is not what `Path.exists()` or a glob answers.
  - Ask its probes rather than `pathlib`; they raise rather than guessing, so a caller owes a boundary.
  - Which to ask where, and at what granularity, is `kboat-vault-conventions` "The write contract".
- Do not add a runtime dependency: zero of them by design is what keeps the core a pure, independently-testable package.
- Ruff is configured workspace-wide in the root `pyproject.toml`; `ty` and pytest in this package's own.
- `mise run qa:py:kboat` runs this member's gate.
  - The repo root's `scripts/coverage_floor.py` is the binding coverage floor, not pytest's own `--cov-fail-under`.
