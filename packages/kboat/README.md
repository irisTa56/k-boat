# kboat — K-Boat's deterministic mechanical core

The tested Python that the [K-Boat](../../README.md) skills call, so the model neither re-derives the mechanical logic nor pays tokens for it.
Its **spec** is split by ownership: the shared vault contract (naming, the schema/validate/write contract, Base discipline) is the `vault-conventions` skill, and K-Boat's note-type field semantics are the `kboat-notes` skill. Change the relevant spec first, then this package and its tests.

## Console scripts

- `kboat-lifecycle` — the distillation lifecycle state machine: boolean/date predicates over frontmatter, no judgement.
- `kboat-repos` — the repo catalogue's mechanics (`gather`/`write`/`refresh`), all shelling out to `gh`.
- `kboat-pick` — the daily-pick mechanics (`candidates`/`set`), no LLM and no NotebookLM.
- `kboat-validate` — checks every vault note against `kboat.schema` and prints violations as JSON.
- `kboat-note` — create-or-update one note from a `{slug, fields, body?}` JSON record.

## Shared modules

- `kboat.frontmatter` — read, scoped rewrite, YAML-safe rendering.
- `kboat.schema` — the code-authoritative mechanical schema (field names, order, kinds, defaults, the always-present booleans, the enums). `vault-conventions` describes the schema/validate/write contract around it, and `kboat-notes` keeps the K-Boat field semantics; both point here.
- `kboat.write` — schema-driven note assembly and create-or-update (`build_note`/`render_field`/`upsert`), the one writer all note types share.

## Development

- Zero runtime dependencies by design, so the core stays a pure, independently-testable package.
- QA: `mise run qa:py:kboat` (ruff, `ty`, pytest); autofix with `mise run fmt:py:kboat`. The workspace-wide gates and layout are in the [root README](../../README.md) and [CLAUDE.md](../../CLAUDE.md).
