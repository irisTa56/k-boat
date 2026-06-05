"""Deterministic K-Boat distillation lifecycle.

This package implements the purely mechanical parts of the `kboat-distill`
routine — the parts decided by boolean and date predicates over source-note
frontmatter, with no judgement required:

- Phase A: maintain the cooldown clock (stamp/clear `filed_date`) and flag
  ambiguous dispositions.
- Compute Phase B's work sets (ripe / dismiss-discard / keep-only no-op),
  excluding blocked (DLQ) sources from both phases.

The schema and the predicates are specified in the `kboat-notes` skill; this
package is an implementation of that spec, not a second source of truth. The
agent still does the judgement-heavy work (distillation, wall-vs-article reads,
the on-demand NotebookLM resolution) — this tool only hands it the work list.
"""
