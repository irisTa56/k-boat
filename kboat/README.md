# kboat

The deterministic, purely-mechanical core of the K-Boat routine, extracted from
the prose skills so the model neither re-derives it nor pays tokens for it. One
package, five console scripts, over a shared frontmatter core
(`kboat.frontmatter`: read, scoped single-line/multi-line rewrite, YAML-safe
rendering), a code-authoritative schema (`kboat.schema`: field names, order,
kinds, defaults, the always-present booleans, the enums), and a schema-driven
writer (`kboat.write`: `build_note`/`render_field`/`upsert`). The **spec** for all of it is the `kboat-notes` skill — change the
spec there first, then the code and its tests.

- `kboat-lifecycle` (`kboat.lifecycle`) — the distillation lifecycle state
  machine: the boolean/date predicates over frontmatter, no judgement. Maintains
  the on-disk cooldown clock (Phase A) and emits the ripe/dismiss/ambiguous source
  and ripe-Kindle work sets as JSON.
- `kboat-repos` (`kboat.repos`) — the repo catalogue's mechanics: subcommands
  `gather`/`write`/`refresh`, all shelling out to `gh`, no LLM.
- `kboat-pick` (`kboat.pick`) — the daily-pick mechanics: `candidates` (the
  Daily-note `## 明日への問い` questions + the active web inbox as JSON) and `set`
  (reset `picked`, then set it on the chosen slugs).
- `kboat-validate` (`kboat.validate`) — checks every note in `Sources/`,
  `Kindles/`, `Repos/` against its schema and prints violations as JSON.
  Read-only; report-only by default, `--strict` exits non-zero.
- `kboat-note` (`kboat.note`) — `write --type {source,kindle,repo}`:
  create-or-update a note from a `{slug, fields, body?}` JSON record (merge over
  existing, stamp the dates, preserve the body, refuse a slug collision).

Each tool defaults the vault to `$OBSIDIAN_VAULT_PATH` and accepts `--today` for
reproducibility. Run the quality gate with `mise run qa:py:kboat` (lint, type
check, and `pytest` over `tests/` — `{lifecycle,repos,pick,validate}/` plus the shared `test_schema.py`).
