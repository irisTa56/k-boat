---
name: kboat-repos
description: Catalogue a GitHub repository into the K-Boat vault, and refresh the repo catalogue's metadata. Use when ingest routes a GitHub repo URL, when the user pastes a `https://github.com/<owner>/<repo>` link or says things like "add this repo", "save this repository", "catalogue this GitHub project", or when they want to refresh repo metadata ("refresh my repo catalogue", "update the repos"). Non-interactive; uses the `gh` CLI. Defers to kboat-notes for the repo-note schema and transitions, and to the kboat-repos package for the deterministic fetch/refresh.
---

# K-Boat repo catalogue

A GitHub repository is a parallel K-Boat kind (`type: repo`): a tagged, searchable bookmark in `Repos/`, with no NotebookLM notebook and no distillation.
This skill owns the two repo operations — cataloguing one repo, and refreshing the whole catalogue's metadata.
It defers to **kboat-notes** for the schema, naming, vocabulary, and transitions, and to the **kboat-repos package** (`.venv/bin/kboat-repos`) for the deterministic mechanics (URL parsing, slug, `status`, the `gh` fetch, the full-catalogue refresh).

The split mirrors the rest of K-Boat: the package does the judgement-free work, this skill does the one piece of judgement (classification), and kboat-notes is the spec both follow.

## Prerequisites

- The [`gh`](https://cli.github.com/) CLI on `PATH`, authenticated (`gh auth status`). Repos use `gh`, not NotebookLM auth.
- `OBSIDIAN_VAULT_PATH` from `.env` (the package's `refresh` defaults to it).

## Procedure: catalogue a repo

Given a GitHub repository URL (from `kboat-ingest` routing, or pasted by the user):

1. **Gather** the metadata: `.venv/bin/kboat-repos gather "<url>"`. It prints a JSON record with the **`gh`-resolved** canonical `url`, `slug`, `title` (`owner/repo`), a ready-to-write `fields` object (the mechanical GitHub-derived frontmatter — `description`, `homepage`, `language`, `topics`, `stars`, `archived`, `created_at`, `last_commit`, `license`, `status` — already mapped, including the 10%-share language rule), and a `readme_excerpt`. A `status` other than `ok` means the URL is not a repo (`skip-not-a-repo`) or `gh` failed (`error-meta`) — report it and stop; do not write a note.
2. **Classify** with a cheap subagent (Haiku — `gh`-fetched repos are a trickle, and repo-memorizer judged the same three fields on Haiku at scale). Give it `fields.description`, `fields.topics`, `fields.language`, and `readme_excerpt`, plus the vocabulary from kboat-notes ("Classification vocabulary"), and have it return:
   - `role` — one of the closed 6-value enum.
   - `domain` — 1–3 values from the controlled 14-word vocabulary; prefer existing values, fold neighbours rather than invent.
   - `summary` — one or two plain Japanese sentences (what it is, who it is for; no marketing language).
3. **Write** via the package: take the gather record, add the judged `role`, `domain`, `summary` keys, and pipe the whole JSON object to `.venv/bin/kboat-repos write` (defaults to `$OBSIDIAN_VAULT_PATH`). The package assembles `Repos/<slug>.md` in the canonical field order, quotes YAML safely (so a colon-bearing `description` can't break the note), de-dups by slug, and preserves an existing note's `## Notes` body / `read` / `added_date` on update — none of which the agent should hand-assemble. It prints `{status: created|updated|collision, ...}`; a `collision` (the slug is held by a different `url`) is written nowhere — report it and stop.

This skill writes only the note; deleting the queue reminder is `kboat-ingest`'s job (its step 4 commit-point rule), and applies once the note exists.

## Procedure: refresh the catalogue

Keep the GitHub-derived metadata fresh (drain ingestion snapshots a repo once):

1. Run `.venv/bin/kboat-repos refresh` (defaults to `$OBSIDIAN_VAULT_PATH`; pass `--dry-run` to preview). It re-fetches every `Repos/*.md` via `gh` in parallel, rewrites only the GitHub-derived frontmatter plus `status` and `refreshed_date`, and **preserves** the judged `role`/`domain`/`summary` and the `## Notes` body. When `gh` resolves a new canonical `owner/repo`, it **adopts the rename**: updates `url`/`title` and renames the file to the new slug, carrying the judgement and body across. It reads the JSON report on stdout.
2. **Relay** the report: counts (`total`/`updated`/`adopted`/`rename_collisions`/`failed`/`anomalies`), and the `adopted` (renames it healed: `was` → `now`, `from` → `to`), `rename_collisions` (a rename blocked because the canonical slug is already taken — a human merges the two notes), and `failed` (repos `gh` could not fetch — deleted/private) entries. The routine never deletes a note.

## Errors

Detect and report; do not work around.

- During ingest routing, `gather` returned `skip-not-a-repo` (a non-repo GitHub URL — profile, gist, reserved route): it is not a repo, so fall through to the source/web path (`kboat-ingest`), not the repo path. `error-meta` (`gh` failed: rate limit, auth, network, or a `github.com/owner/repo` that does not resolve): no note is written — report it and keep the reminder for retry; do not ingest it as a web page.
- `write` returned `status: collision` (the slug is held by a different `url`). Nothing was written; report it — deterministic, needs a human.
- `refresh` `failed` entries (a `gh` error per repo), `rename_collisions` (a rename blocked by an existing note), and `adopted` (renames healed) — surface them; never delete.
- `gh` not authenticated. Stop and report rather than producing empty records.
