# CLAUDE.md

The `kboat` package — K-Boat's deterministic mechanical core (the library that the K-Boat skills call).
This file covers only what is specific to working on the library; the K-Boat product architecture and the shared workspace conventions are in the [root CLAUDE.md](../../CLAUDE.md).

## What this package is

The library surface — its five console scripts and three shared modules (`kboat.frontmatter`, `kboat.schema`, `kboat.write`) — is described in [README.md](README.md).
Its **spec** is the `kboat-notes` skill (at the repo-root `.claude/skills/`): the schema, naming, and lifecycle semantics live there.

## Working on it

- Change the spec first. `kboat-notes` is the source of truth for the note schema and conventions — edit it, then the code (`src/kboat/`) and its tests (`tests/`), then reconcile the schema tables (the `test_doc_schema_sync` gate checks them against `kboat.schema`).
- Zero runtime dependencies by design, so the core stays a pure, independently-testable package. Do not add a runtime dependency.
- Dev/QA conventions (ruff, `ty`, pytest, the coverage gate) are workspace-wide — see the [root CLAUDE.md](../../CLAUDE.md). Run this member's gate with `mise run qa:py:kboat`.
