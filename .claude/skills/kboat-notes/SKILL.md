---
name: kboat-notes
description: Conventions for creating and updating K-Boat notes. Use when creating or updating a source, Kindle, or GitHub repo note, discarding a source's notebook, or when you need the frontmatter schema, naming rules, lifecycle state, the cross-field validation rules, the backlog-health counts `kboat-validate --stats` reports, the Sources, Kindle, Repos, or Reviews Base, where distilled concept notes live, or how a concept note's `## Observations` divides into reading groups. This is the source of truth for K-Boat's note types and their lifecycle; the shared vault mechanics are the kboat-vault-conventions skill, which this defers to.
---

# K-Boat note conventions

K-Boat reads content through Google NotebookLM and matures what it learns into a knowledge base.
Each piece of content gets one NotebookLM notebook all to itself (1:1), plus one source note in the Obsidian vault that tracks it.
The notebook is a throwaway reading-and-dialogue workspace; the durable record is the source note and, after distillation, the concept notes.

Most content is a **source** read through NotebookLM as above. Two parallel kinds are exceptions, each with no notebook:

- A **Kindle book** is read on a Kindle, has no fetched URL, and is tracked by a `type: kindle` note in `Kindles/` that distillation draws on from highlights captured in the note body.
- A **GitHub repository** is a tagged, searchable catalogue entry — a `type: repo` note in `Repos/` carrying GitHub metadata plus a judged role/domain/summary. It is never read through NotebookLM and never distilled into the knowledge graph; it is a bookmark you can browse and search. See [Repo note](references/repo-note.md#repo-note-reposmd).

Where this skill says "source" it means a `Sources/*.md` note; the Kindle and repo kinds have their own references ([Kindle note](references/kindle-note.md#kindle-note-kindlesmd), [Repo note](references/repo-note.md#repo-note-reposmd)) and their own procedures.

## Environment

- The vault path is `OBSIDIAN_VAULT_PATH`, read from `.env` (an iCloud Obsidian vault). The value in `mise.toml` is only a default and is overridden by `.env`.
- The knowledge path is `KBOAT_KNOWLEDGE_PATH`, read from `.env`. It holds the distilled concept notes and may live outside the vault (for K-Boat it is a Git-managed directory). When unset, default to `<OBSIDIAN_VAULT_PATH>/Knowledge`.
- Run `eval "$(mise env)"` at the top of any shell block that calls a project CLI, then invoke the CLIs bare — `notebooklm`, `kboat-lifecycle`, `kboat-repos`. `mise env` exports `.env` over `mise.toml`'s defaults and puts both the uv venv (`.venv/bin`, the `kboat-*` scripts) and the mise tools (the `notebooklm` CLI, the `pipx:notebooklm-py` tool) on `PATH`, so the bare names resolve and `$OBSIDIAN_VAULT_PATH` expands inside arguments (no `.venv/bin/` prefix, no `--vault` flag). The Bash tool keeps no shell state between calls, so re-run `eval "$(mise env)"` in each block. Without this on `PATH`, a bare `notebooklm` fails.
- When parsing `--json` output, pass the global `--quiet` flag (`notebooklm --quiet … --json`): some subcommands (e.g. `source list`) otherwise print status to stdout, where it corrupts the JSON. **`--quiet` reaches the CLI's own output and not the library beneath it**, which writes to stderr — an `UnknownTypeWarning` naming a source kind the installed version does not know (notebooklm-py 0.7.3 does not know the one a saved note carries, so this fires for every notebook holding reading-time dialogue), or an `ERROR … rpc_code=…` line ahead of a failure. The Bash tool merges the two streams, so an agent deciding whether a call succeeded meets that text first, and a warning naming a version problem reads like a failure. Redirect stderr (`2>/dev/null`) wherever a decision turns on the output: both the success payload and the `--json` error object come back on stdout, so nothing is lost.
- For CLI usage and authentication details, see the `notebooklm-py` skill.

## Layout

K-Boat spans two roots.

The Obsidian vault (`OBSIDIAN_VAULT_PATH`) holds the reading side:

- `Sources/` — one note per source. Each tracks one piece of content and its 1:1 notebook.
- `PDFs/` — the downloaded file for each PDF source, named `<slug>.pdf` by the same URL-hash slug as its note. Only PDF sources have one; web-page sources do not. It is the reading copy (opened in Obsidian) and the file uploaded to NotebookLM.
- `Reviews/` — one `YYYY-MM-DD.md` per run that distilled something, the review report read for memory consolidation: the distillation knowledge log only (per-source/Kindle consolidation plus decision log), not operational telemetry, which stays in the run summary. A run that distilled nothing writes no file. Covers both source and Kindle distillation. Carries a small `type: review`/`date`/`read` frontmatter block for the read-tracking Base (see [Review note](references/review-note.md#review-note-reviewsmd)); the body layout is kboat-distill "Review report".
- `Reviews.base` — a top-level standalone Base listing the review reports with their read flag (see [Review Base](references/bases.md#review-base)).
- `Sources.base` — a top-level standalone Base listing sources still to read (see [Sources Base](references/bases.md#sources-base)).
- `Kindles/` — one `type: kindle` note per Kindle book, named by ASIN. No notebook; read on a Kindle and distilled from highlights pasted into the note body. See [Kindle note](references/kindle-note.md#kindle-note-kindlesmd).
- `Kindles.base` — a top-level standalone Base listing Kindle books (see [Kindle Base](references/bases.md#kindle-base)).
- `Repos/` — one `type: repo` note per GitHub repository, named by a URL hash. No notebook; a metadata catalogue entry, not distilled. See [Repo note](references/repo-note.md#repo-note-reposmd).
- `Repos.base` — a top-level standalone Base listing GitHub repositories (see [Repo Base](references/bases.md#repo-base)).

The knowledge root (`KBOAT_KNOWLEDGE_PATH`) holds the distilled side:

- Concept notes, accreted across sources, managed as a Basic Memory knowledge graph. The generic note conventions belong to the Basic Memory skills (`memory-notes`, `memory-ingest`, `memory-curate`), to which kboat-distill defers; what `## Observations` looks like once more than one reading has fed a note is this skill's own. See [Concept notes](references/concept-notes.md#concept-notes-kboat_knowledge_path).

## Conventions

The vault-wide conventions — `snake_case` keys, the `_date` / `YYYY-MM-DD` rule, inline vs block lists, and the URL-hash naming recipe (`kboat-note slug "<url>"`) — are the shared `kboat-vault-conventions` skill; read it for the mechanics.
What is specific to K-Boat notes:

- A source note is `Sources/<slug>.md` and a repo note `Repos/<slug>.md`, both named by `kboat-note slug` over the note's own `url` — the queued URL for a source, the constructed canonical GitHub URL for a repo (see [Naming and de-dup](references/repo-note.md#naming-and-de-dup)). Because that `url` is immutable the file is never renamed, and the create procedure de-dups by reading the existing note's `url`, never by filename alone.
- A Kindle note is named by its ASIN (`Kindles/<ASIN>.md`) and a review report by its date (`Reviews/YYYY-MM-DD.md`) — the non-hash exceptions.

## References

Everything above is what any writer needs; the rest is in `references/`, read one at a time as the work reaches it.

| Reference | What it owns |
| --- | --- |
| [Source note](references/source-note.md) | The `Sources/*.md` schema, the 1:1 notebook rule and how a notebook's original is identified, the dispositions and their cooldown, and the DLQ. |
| [Kindle note](references/kindle-note.md) | The `Kindles/*.md` schema and its notebook-free lifecycle. |
| [Repo note](references/repo-note.md) | The `Repos/*.md` schema, the `gh`-resolved naming and de-dup, the `role`/`domain` vocabulary, and what a refresh preserves. |
| [Review note](references/review-note.md) | The frontmatter block a review report carries, and why it is mandatory. |
| [Concept notes](references/concept-notes.md) | What a distilled note's `## Observations` looks like once several readings have fed it, how provenance and grounding are tagged, and the math-notation rule. |
| [Validation](references/validation.md) | What `kboat-validate` reports: the cross-field rules, and the backlog-health counts `--stats` adds. |
| [Daily pick](references/daily-pick.md) | How the routine chooses at most two web sources to surface for today. |
| [Bases](references/bases.md) | The four standalone Bases, their views, and the filter discipline each follows. |
| [Procedures](references/procedures.md) | Every procedure this skill owns — ingest, rescue, restore, reactivate, discard, and the create/update path for each note type. |
