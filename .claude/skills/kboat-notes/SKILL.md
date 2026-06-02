---
name: kboat-notes
description: Conventions for creating, updating, and deleting K-Boat notes in the Obsidian vault. Use when creating a notebook note, creating, updating, or deleting a source note, or when you need the exact frontmatter schema, naming rules, or Dashboard Base for K-Boat. This is the single source of truth for K-Boat note management; the kboat-ingest skill defers to it for writing notes.
---

# K-Boat note conventions

K-Boat stores NotebookLM metadata as notes in an Obsidian vault.
Each NotebookLM notebook and each of its sources gets one Markdown note.

## Environment

- The vault path is `OBSIDIAN_VAULT_PATH`, read from `.env` (an iCloud Obsidian vault). The value in `mise.toml` is only a default and is overridden by `.env`.
- The NotebookLM CLI is at `.venv/bin/notebooklm`. Always invoke it by that path; the bare `notebooklm` fails because the mise shim is not configured. Every `notebooklm` command in this skill assumes the `.venv/bin/` prefix.
- For CLI usage and authentication details, see the `notebooklm-py` skill.

## Vault layout

Two top-level directories:

- `Notebooks/` — one note per NotebookLM notebook.
- `Sources/` — one note per source. A source may belong to several notebooks.

## Conventions

- Property keys and enum values use `snake_case`.
- Dates (`created`, `added`) use `YYYY-MM-DD`.
- Derive a filename from the title by replacing each character Obsidian disallows in note names — `/ \ : * ? " < > |` — with `-`. Keep the exact title in the `title` property.

## Notebook note (`Notebooks/*.md`)

Frontmatter, in this exact order:

| Property | Meaning |
| --- | --- |
| `type` | Always `notebook`. |
| `title` | Notebook title as shown in NotebookLM. |
| `gemini_url` | Gemini chat view of the notebook, used for asking questions while reading. |
| `notebooklm_id` | NotebookLM notebook id. |
| `notebooklm_url` | NotebookLM view of the notebook. |
| `created` | Creation date. |
| `active` | Checkbox. Whether the notebook is in active use. |
| `description` | One-line summary. Also the key signal kboat-ingest uses to route sources. |
| `tags` | Empty for now. |

`gemini_url` and `notebooklm_url` share the same id and path; they differ only by subdomain.
Derive both from the id:

- `notebooklm_url`: `https://notebooklm.google.com/notebook/<id>`
- `gemini_url`: `https://gemini.google.com/notebook/<id>`

The body has two sections in this order:

1. `## Dashboard` — the embedded Base below.
2. `## Sources` — a list of `[[wikilinks]]` to the source notes. This list is the source of truth for notebook membership.

### Dashboard Base

````markdown
```base
filters:
  and:
    - type == "source"
    - this.file.hasLink(file)
    - done != true
views:
  - type: table
    name: Sources
    order:
      - file.name
      - read
      - done
```
````

`this.file.hasLink(file)` selects the sources this notebook links to, which is the notebook to source direction.
`done != true` keeps the view to an inbox of unprocessed sources.

## Source note (`Sources/*.md`)

Frontmatter only, no body. The order groups the manually edited fields under `title`:

| Property | Meaning |
| --- | --- |
| `type` | Always `source`. |
| `title` | The source title (the page's title; matches NotebookLM's title once the source is added). |
| `reading_url` | Where to read. Starts equal to `url`, then overwritten with a "Link with Highlight" as reading progresses. For PDFs it may point to a Google Play Books link instead. |
| `read` | Checkbox. The source has been read. |
| `done` | Checkbox. The source has been dealt with, whether or not it was read. |
| `source_type` | e.g. `web_page`. |
| `url` | Original, canonical URL. Immutable. |
| `added` | Date the source was ingested. |
| `tags` | Empty for now. |

`read` and `done` are independent, not a sequence.
A source you want in NotebookLM but do not intend to read fully gets `done` checked without `read`.

## Link direction and orphans

Links run from notebook to source, matching NotebookLM's model where a notebook contains sources.
The reverse lookup comes from Obsidian backlinks, so a source is not required to reference its notebooks, and one source can belong to several notebooks.
A source with no backlinks belongs to no notebook and shows up as an orphan; this is the signal that it still needs a notebook.

The NotebookLM source id is not stored. It is a per-notebook attribute (the same URL added to two notebooks gets two ids), so it is resolved on demand by matching the source's `url` in a notebook's `.venv/bin/notebooklm source list` (see the delete procedure).

## Procedure: create a notebook note

1. De-duplicate before creating:
   - If `Notebooks/<sanitized-title>.md` already exists, stop and use it.
   - Otherwise run `.venv/bin/notebooklm list --json` (notebooks are under `.notebooks[]`, each with `.id` and `.title`) and look for one with the same title; if it exists, reuse its id rather than creating a duplicate.
2. If no existing notebook was found, run `.venv/bin/notebooklm create "<title>" --json` and read `.notebook.id`.
3. Write `Notebooks/<sanitized-title>.md` with the frontmatter above, deriving both URLs from the id, with `active: true` and the given `description`.
4. Include the `## Dashboard` Base and an empty `## Sources` section.

## Procedure: create or update a source note

1. De-duplicate by `url`: scan existing `Sources/*.md` frontmatter for a note with this `url`. If one exists, update it in place rather than creating a new note; if its title changed, rename the file to the new sanitized title.
2. Otherwise write a new `Sources/<sanitized-title>.md` with the frontmatter above. If that filename is already taken by a note with a different `url`, disambiguate with a short suffix. `reading_url` starts equal to `url`; `read` and `done` start unchecked.
3. When the source is added to a notebook with `.venv/bin/notebooklm source add`, add a `[[wikilink]]` to that notebook's `## Sources` section. The returned source id is not stored; it is resolved on demand (see the delete procedure).

## Procedure: remove a source from notebooks

This removes a source from one or more NotebookLM notebooks and detaches it in the vault.
The `Sources/*.md` note is always kept; once no notebook links it, it becomes an orphan, which is the unrouted state.

Identify the source by its note. Accept a name or a URL as input and resolve it to the source note: a name matches the `title` or filename, a URL matches the `url` property.
Prefer the name, since uploaded PDFs may have no URL.

1. Resolve the source note from the input. If several notes match, ask which one.
2. List the notebooks that contain it: the note's backlinks (notebooks whose `## Sources` link it).
3. Ask the user which of those notebooks to remove it from: one, several, or all.
4. For each selected notebook:
   - Resolve its id: run `.venv/bin/notebooklm source list --notebook <notebooklm_id> --json` and match the source note's `url`, falling back to `title`. If nothing matches, the NotebookLM source is already gone, so skip the delete and only clean up the wikilink.
   - Run `.venv/bin/notebooklm source delete <id> --notebook <notebooklm_id>`.
   - Remove the `[[wikilink]]` from that notebook note.
5. Keep the source note.

This flow is interactive, so it belongs to a manual invocation rather than the unattended routine.
