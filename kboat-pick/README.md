# kboat-pick

Deterministic mechanics for the K-Boat **daily pick** — the routine step that
turns "what do I read first?" into a pull: you write questions in a Daily note
and the next run surfaces the web sources that answer them. The relevance ranking
is an LLM step in the `kboat-recall` skill; this package does the mechanical I/O
around it so the model neither re-derives it nor pays tokens for it. The spec is
`kboat-notes` ("Daily pick").

Console script: `kboat-pick`. Vault defaults to `$OBSIDIAN_VAULT_PATH`.

## Subcommands

- `kboat-pick candidates [--today YYYY-MM-DD]` — print, as JSON, the Daily-note
  `## 明日への問い` questions (newest-first, dated on or before `--today`) and the
  active web inbox (`!distill && !keep && !dismiss && !blocked && source_type ==
  "web_page"`), each candidate carrying its `summary`/`topics` for the ranker.
- `kboat-pick set --slugs a,b` — reset `picked` to `false` on every source, then
  set it `true` on the chosen slugs (at most two). An empty `--slugs` just clears
  the spotlight. Reports `picked`, `missing` (slugs with no source), and `reset`.

Both accept `--vault` and `--today` for reproducibility, mirroring
`kboat-lifecycle`. `set` rewrites only the single `picked:` line in a note's
frontmatter (inserting one after `blocked:` if absent), leaving field order and
the body intact.
