---
name: kboat-notes
description: Conventions for creating and updating K-Boat notes. Use when creating or updating a source note, discarding a source's notebook, or when you need the exact frontmatter schema, naming rules, lifecycle state, the reading inbox Base, or where distilled concept notes live. This is the single source of truth for K-Boat note management; kboat-ingest and kboat-distill defer to it.
---

# K-Boat note conventions

K-Boat reads content through Google NotebookLM and matures what it learns into a knowledge base.
Each piece of content gets one NotebookLM notebook all to itself (1:1), plus one source note in the Obsidian vault that tracks it.
The notebook is a throwaway reading-and-dialogue workspace; the durable record is the source note and, after distillation, the concept notes.

## Environment

- The vault path is `OBSIDIAN_VAULT_PATH`, read from `.env` (an iCloud Obsidian vault). The value in `mise.toml` is only a default and is overridden by `.env`.
- The knowledge path is `KBOAT_KNOWLEDGE_PATH`, read from `.env`. It holds the distilled concept notes and may live outside the vault (for K-Boat it is a Git-managed directory). When unset, default to `<OBSIDIAN_VAULT_PATH>/Knowledge`.
- The NotebookLM CLI is at `.venv/bin/notebooklm`. Always invoke it by that path; the bare `notebooklm` fails because the mise shim is not configured. Every `notebooklm` command in this skill assumes the `.venv/bin/` prefix.
- When parsing `--json` output, pass the global `--quiet` flag (`.venv/bin/notebooklm --quiet … --json`) so status output does not corrupt the JSON. Some subcommands (e.g. `source list`) print status to stdout otherwise.
- For CLI usage and authentication details, see the `notebooklm-py` skill.

## Layout

K-Boat spans two roots.

The Obsidian vault (`OBSIDIAN_VAULT_PATH`) holds the reading side:

- `Sources/` — one note per source. Each tracks one piece of content and its 1:1 notebook.
- `Reviews/` — one `YYYY-MM-DD.md` per distillation run, the review report read for memory consolidation.
- `Reading Inbox.base` — a top-level standalone Base listing sources still to read (see below).

The knowledge root (`KBOAT_KNOWLEDGE_PATH`) holds the distilled side:

- Concept notes, accreted across sources, managed as a Basic Memory knowledge graph. The concept-note conventions belong to the Basic Memory skills (`memory-notes`, `memory-ingest`, `memory-curate`), to which kboat-distill defers; see "Concept notes" below.

## Conventions

- Property keys and enum values use `snake_case`.
- Date-valued properties carry a `_date` suffix (`added_date`, `done_date`, `distilled_date`) and use `YYYY-MM-DD`.
- Derive a filename from the title by replacing each character Obsidian disallows in note names — `/ \ : * ? " < > |` — with `-`. Keep the exact title in the `title` property.

## One notebook per source (1:1)

Every source has exactly one NotebookLM notebook, created when the source is ingested.
The notebook's coordinates (`notebooklm_id`, `gemini_url`, `notebooklm_url`) live on the source note, so the source note is self-contained — there are no notebook notes, no wikilinks between notebooks and sources, and no backlink-based reverse lookup.
Reading-time questions go to the notebook's `gemini_url`, and because the notebook holds only this one source, the answers are never diluted by unrelated content.

The NotebookLM source id is not stored. It is a per-notebook attribute resolved on demand by matching the source's `url` (then `title`) in `notebooklm source list` (see the discard and distill procedures).

## Source note (`Sources/*.md`)

Frontmatter only, no body. Fields are ordered for reading — the URLs you open and the read/done checkboxes first, then the source metadata, then the dates and notebook coordinates the routine manages.

| Property | Meaning |
| --- | --- |
| `type` | Always `source`. |
| `title` | The source title (the page's title; matches NotebookLM's title once the source is added). |
| `reading_url` | Where to read. Starts equal to `url`, then overwritten with a "Link with Highlight" as reading progresses. For PDFs it may point to a Google Play Books link instead. |
| `gemini_url` | Gemini chat view of the notebook, used for asking questions while reading. |
| `read` | Checkbox, set by the human. The source has been read. |
| `done` | Checkbox, set by the human. The source is ready to be processed, whether or not it was read. |
| `source_type` | e.g. `web_page`. |
| `url` | Original, canonical URL. Immutable. |
| `added_date` | Date the source was ingested. |
| `done_date` | Date, stamped by the routine when it first observes `done`. Empty until then. The clock that ripeness counts from. |
| `distilled_date` | Date, stamped by the routine when distillation completes. Empty until then. Terminal marker. |
| `notebooklm_id` | NotebookLM notebook id for this source's 1:1 notebook. Cleared once the notebook is discarded. |
| `notebooklm_url` | NotebookLM view of the notebook. |
| `tags` | Empty for now. |

`gemini_url` and `notebooklm_url` share the same id and path and differ only by subdomain.
Derive both from `notebooklm_id`:

- `notebooklm_url`: `https://notebooklm.google.com/notebook/<id>`
- `gemini_url`: `https://gemini.google.com/notebook/<id>`

### Lifecycle and state

`read` and `done` are independent checkboxes the human sets; `done_date` and `distilled_date` are dates the routine stamps.
`done_date` records when the routine first observed `done`, not when the human checked it — a checkbox carries no timestamp — so the cooldown below counts from that first observation, and a stretch where the routine cannot run delays it.
The routine (kboat-distill) drives the transitions:

- The human checks `done`. On its next run the routine stamps `done_date` if empty, starting a 7-day cooldown.
- Once `done_date` is at least 7 days old the routine acts, branching on `read`:
  - `read` checked → the source is **ripe**: distill it, stamp `distilled_date`, then discard the notebook.
  - `read` unchecked → it will not be read: discard the notebook without distilling (nothing to distill), leaving `distilled_date` empty.
- Either branch ends by discarding the notebook and clearing `notebooklm_id`.

The ripe predicate is `done == true && read == true && done_date <= today - 7 days && distilled_date` empty.
Terminal and in-flight states are readable from frontmatter: `distilled_date` set means distilled; `done` with `notebooklm_id` empty and `distilled_date` empty means discarded unread; `done` with `notebooklm_id` still set means in flight — awaiting the cooldown, or ripe and being retried after a recorded error.
For a ripe source the notebook is discarded last, after `distilled_date` is stamped and the review report is written, so nothing it holds is destroyed before it is recorded.

## Reading inbox Base

A single standalone Base at the vault root, `Reading Inbox.base`, replaces the old per-notebook dashboards with two views over all sources: the to-read inbox, and a processed view that makes the otherwise-invisible lifecycle states (in flight, distilled, discarded unread) legible from their columns.
Both views lead with the `read`/`done` checkboxes and sort by `added_date`. Column widths and other cosmetics are per-vault tweaks.

```yaml
filters:
  and:
    - type == "source"
views:
  - type: table
    name: Reading Inbox
    filters:
      and:
        - done != true
    order:
      - read
      - done
      - file.name
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Processed
    filters:
      and:
        - done
    order:
      - read
      - done
      - file.name
      - added_date
      - done_date
      - distilled_date
      - notebooklm_id
    sort:
      - property: added_date
        direction: DESC
```

## Procedure: create or update a source note

1. De-duplicate by `url`: scan existing `Sources/*.md` frontmatter for a note with this `url`. If one exists, update it in place rather than creating a new note; if its title changed, rename the file to the new sanitized title. If it already has a `notebooklm_id`, it already has a notebook — do not create a second one.
2. Otherwise write a new `Sources/<sanitized-title>.md`. If that filename is already taken by a note with a different `url`, disambiguate with a short suffix. `reading_url` starts equal to `url`; `read` and `done` start unchecked; `done_date` and `distilled_date` start empty.
3. Create the 1:1 notebook and record its coordinates:
   - Run `.venv/bin/notebooklm --quiet create "<title>" --json` and read `.notebook.id`.
   - Run `.venv/bin/notebooklm --quiet source add "<url>" --notebook <id> --json` to add the one source.
   - Write `notebooklm_id` onto the source note and derive `gemini_url` and `notebooklm_url` from it. The returned source id is not stored; it is resolved on demand.

## Procedure: discard a source's notebook

This deletes the source's 1:1 NotebookLM notebook and clears its coordinates. The `Sources/*.md` note is always kept.
Used when a source is dealt with but not read (`done` without `read`), or as the final step of distillation.

1. Read `notebooklm_id` from the source note. If empty, the notebook is already gone — nothing to do.
2. Run `.venv/bin/notebooklm delete --notebook <notebooklm_id> -y`.
3. Clear `notebooklm_id`, `gemini_url`, and `notebooklm_url` on the source note.

## Concept notes (`KBOAT_KNOWLEDGE_PATH`)

Distillation writes concept notes into the Basic Memory project `k-boat-knowledge`, rooted at `KBOAT_KNOWLEDGE_PATH`.
The concept-note format and the accretion procedure are defined by the Basic Memory skills — kboat-distill defers to `memory-notes` (note structure), `memory-ingest` (entity matching), and `memory-curate` (merging), the same way kboat-ingest defers to this skill.

Relations between concepts use wikilinks (`- relation_type [[Other Concept]]`); both ends live in this same root, so they resolve in Basic Memory, Obsidian, and Foam.
Provenance back to a source is different: the source note lives in the vault, a separate root, so a wikilink to it could not resolve. Record provenance instead as an observation carrying the source's canonical URL, e.g. `- [source] <title> — <url>`. This is root-independent, stable, and greppable.
Tag each distilled observation by grounding — `#grounded` for claims the source supports, `#dialogue` for external knowledge the reading-time conversation surfaced — so a chat-derived claim is never mistaken for a source claim (kboat-distill defines how the two are sorted and verified).

These notes are plain Markdown and degrade gracefully: the `## Observations` lines (`- [category] content #tag`) and the in-root relation wikilinks read as ordinary bullets and working links in Obsidian or Foam, so the knowledge stays browsable even without the Basic Memory runtime, which is only the search layer.
