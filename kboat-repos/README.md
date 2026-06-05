# kboat-repos

The deterministic core of the K-Boat GitHub-repo catalogue.

A GitHub repository is a parallel K-Boat kind (`type: repo`) — a tagged,
searchable catalogue entry with no NotebookLM notebook and no distillation,
alongside sources and Kindle books. This package holds the parts of that kind
decided without judgement; the classification (role/domain/summary) stays with
the `kboat-repos` skill's cheap subagent.

The schema and predicates are **specified** in the `kboat-notes` skill; this
package implements that spec. When the spec changes, update the code and tests.

## Usage

```sh
kboat-repos gather <github-url>      # one repo's canonical id + metadata + README -> JSON
kboat-repos write [--vault PATH]     # gather record + classification (stdin JSON) -> Repos/<slug>.md
kboat-repos refresh [--vault PATH]   # re-fetch every Repos/*.md, recompute status, adopt renames
```

- `gather` resolves the URL to its canonical `owner/repo` via `gh` (so renames,
  transfers, and case normalize), runs `gh repo view` + the README API, and
  prints a JSON record including a ready-to-write `fields` object. The
  `kboat-repos` skill feeds that to a subagent that judges role/domain/summary.
- `write` reads that record (augmented with the classification) on stdin and
  writes `Repos/<slug>.md` — guaranteeing frontmatter order, YAML quoting,
  de-dup, and `## Notes` body preservation, so the agent never hand-writes YAML.
- `refresh` re-fetches every `Repos/*.md` note (defaults to
  `$OBSIDIAN_VAULT_PATH`), rewrites only the GitHub-derived frontmatter plus
  `status` and `refreshed_date`, and **preserves** the judgement layer
  (role/domain/summary) and the `## Notes` body. It **adopts** a renamed/
  transferred/case-changed repo (updates `url`/`title`, renames the file to the
  new canonical slug); a slug collision or an unfetchable repo is reported, never
  patched, and no note is ever deleted. `--dry-run` fetches and reports without
  writing.

`gather` and `refresh` require the [`gh`](https://cli.github.com/) CLI on `PATH`, authenticated; `write` does not (it only touches the vault).

The one-time migration of the legacy `repo-memorizer` catalogue was done by a
throwaway script (run once, then deleted), not a subcommand — single-use code
does not live here.

## Development

From the repo root, `mise run qa:py:kboat-repos` runs ruff, `ty`, and pytest
(part of the pre-commit gate); `mise run fmt:py` autofixes lint and formatting.
The pure helpers (`identity`, `status`, `notes`, the README/payload mappers in
`gather`) are unit-tested; the `gh` calls are not.
