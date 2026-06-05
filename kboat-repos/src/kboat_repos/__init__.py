"""Deterministic K-Boat repo-catalog helper.

A GitHub repository is a parallel K-Boat kind (`type: repo`) — a catalogue
entry with no NotebookLM notebook and no distillation, sitting alongside
sources and Kindle books (see the `kboat-notes` skill). This package holds the
purely mechanical parts of that kind, the parts decided without judgement:

- `identity`: parse a GitHub URL to `owner/repo`, build the canonical URL, and
  hash it to the note slug — the same URL-hash recipe sources use, so the two
  kinds share one de-dup story.
- `status`: bucket a repo by days since last push (`recent`/`active`/`slow`/
  `dormant`/`archived`/`unknown`).
- `gather`: fetch a single repo's metadata + README excerpt via `gh`, for the
  `kboat-repos` skill to classify (role/domain/summary) and write.
- `refresh`: re-fetch every `Repos/*.md` note's GitHub-derived frontmatter and
  recompute `status`, preserving the judgement layer (role/domain/summary) and
  the human-edited `## Notes` body.

The schema and the predicates are specified in the `kboat-notes` skill; this
package is an implementation of that spec, not a second source of truth. The
judgement work (classification) stays with the agent — this tool only fetches
and computes.
"""
