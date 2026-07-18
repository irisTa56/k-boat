# CLAUDE.md

Workspace root for a personal reading pipeline: two products that share one Obsidian vault.
This umbrella file holds only what is common to every member; each package's own `CLAUDE.md` holds its specifics.

## What this repo is

A uv workspace (managed with mise + uv). Two member packages under `packages/`, each a self-contained product plus its deterministic Python core:

- **K-Boat** (`packages/kboat/`) — reads content through Google NotebookLM and matures it into a Basic Memory knowledge base. See [packages/kboat/CLAUDE.md](packages/kboat/CLAUDE.md).
- **feed-filter** (`packages/feed-filter/`) — triages new pages from registered feeds/forums into the vault. See [packages/feed-filter/CLAUDE.md](packages/feed-filter/CLAUDE.md).

Both write Obsidian notes to the same vault (`OBSIDIAN_VAULT_PATH`, from `.env`).

## Layout and responsibilities

- **Root** — shared only: the uv workspace (`pyproject.toml`), the toolchain and QA (`mise.toml`, `.rumdl.toml`, `lychee.toml`, `.github/`), one `LICENSE`, this umbrella `CLAUDE.md`, and `.claude/skills/` (all product skills, both members).
- **`packages/<member>/`** — that product's Python package (`src/`, `tests/`, `pyproject.toml`), its own `CLAUDE.md`, and any member-specific docs and assets.

Product skills stay at the repo-root `.claude/skills/`, not in a package: Claude Code only surfaces a nested `packages/x/.claude/skills/` skill when working under that dir, and a scheduled task cannot invoke it by unqualified name — so a globally-invoked product skill has to live at the root.

## Environment

- Run `eval "$(mise env)"` at the top of any shell block that calls a project CLI, then invoke it bare. It loads `.env` over `mise.toml`'s defaults and puts the single workspace `.venv` (both members' console scripts) on `PATH`. Re-run it per block — the Bash tool keeps no state.

## Commands

- `mise install` — install tools, then a postinstall hook runs `uv sync` (installs both members editable into the one workspace venv) and generates the git pre-commit hook.
- Quality gates (`mise run pre-commit` runs them all; the git pre-commit hook calls it, so a failure blocks commits):
  - `mise run qa:md` / `fmt:md` — markdown lint / autofix (rumdl).
  - `mise run qa:secrets` — gitleaks over staged changes.
  - `mise run qa:py` / `fmt:py` — ruff + ty + pytest across both members; per-member as `qa:py:kboat` / `qa:py:feed-filter` (and `fmt:py:*`).
- `mise run check:links` — lychee link check (network; not in pre-commit).
- Member-specific commands (e.g. NotebookLM auth) live in that member's `CLAUDE.md`.

## Tooling config

- One `[tool.ruff]` at the root `pyproject.toml`; members carry none and inherit it by directory walk-up, so the coding style is identical everywhere. `.rumdl.toml` and `lychee.toml` are workspace-wide.
- pytest and ty config stay per-member — coverage gates, test paths, and type-check roots are package-specific.

## Git workflow

- Never push to `main` directly; branch first, then PR. PRs are merged out-of-band, so verify the current branch before pushing — a merge can leave the tree on `main`.

## Writing conventions

- In markdown prose (docs and skills), do not break a line mid-sentence; line breaks go only at sentence boundaries.
- Property keys and enum values are `snake_case`; dates are `YYYY-MM-DD`. Member-specific naming (note filenames, etc.) is in that member's `CLAUDE.md`.

## Keep this file current

Treat every `CLAUDE.md` as part of the definition of done. Update this umbrella when a change alters the workspace layout, the shared toolchain or commands, or the shared conventions; update a member's `CLAUDE.md` when its own architecture or commands change.
