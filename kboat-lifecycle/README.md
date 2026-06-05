# kboat-lifecycle

The deterministic core of the K-Boat distillation pass.

The distillation lifecycle — when a filed source becomes ripe, when a dismissed
one is discarded, when dispositions contradict — is decided entirely by boolean
and date predicates over source-note frontmatter. That needs no model judgement,
so it lives here as tested Python rather than as skill prose: the `kboat-distill`
routine runs this once per pass instead of reading every note and doing the date
math itself.

The predicates are **specified** in the `kboat-notes` skill; this package
implements that spec. When the spec changes, update the code and its tests to
match.

## Usage

```sh
kboat-lifecycle [--vault PATH] [--today YYYY-MM-DD] [--dry-run]
```

- Reads every `Sources/*.md` under the vault (`--vault`, or `$OBSIDIAN_VAULT_PATH`).
- **Phase A (on disk):** stamps `filed_date` with today on newly-dispositioned
  sources and clears it where every disposition was unchecked. `--dry-run` skips
  these writes.
- **Prints JSON** on stdout: `phase_a.stamped`/`cleared`, `ambiguous`,
  `phase_b.ripe`, `phase_b.dismiss_discard`, plus `counts` and `anomalies`.
- Blocked (DLQ) sources are excluded from both phases.

## Development

From the repo root, `mise run qa:py:kboat-lifecycle` runs ruff, `ty`, and pytest
(it is part of the pre-commit gate); `mise run fmt:py` autofixes lint and
formatting.
