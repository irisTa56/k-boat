---
name: kboat-recall
description: Find shelved "read later" sources by a natural-language question. Use when the user asks whether they saved something to read on a topic, wants to surface kept sources matching a question, or says things like "do I have anything about X to read", "find that paper I shelved", "what's on my read-later shelf about Y". Searches source-note title/summary/topics; defers to kboat-notes for the schema.
---

# K-Boat recall

Answer "do I have something saved to read about X?" by searching the source notes.
A K-Boat source marked `keep` is the **read-later shelf** — kept as a searchable archive entry whose notebook is retained for re-reading and dialogue (see kboat-notes lifecycle). Its `summary` and `topics`, captured at ingest, are the durable signal this skill searches.

Follow the kboat-notes skill for the source-note schema and the lifecycle. This skill is read-only — it never edits notes or touches NotebookLM.

## Scope

- **Default: `keep`.** Search sources with `keep == true` — the read-later shelf the user chose to hold (this covers `keep` alone and `keep` + `distill`). These are the ones parked to read later.
- **Specify the states.** A `--states <list>` argument redirects the scope to the union of the named dispositions, drawn from `keep`, `distill`, `active`, `dismiss` (comma-separated, e.g. `--states keep,distill`). `active` is the undispositioned inbox, computed as `!distill && !keep && !dismiss && !blocked` — there is no `active` property, so derive it from the flags. Include `dismiss` only when the user names it explicitly: it is the abandoned pile, deliberately out of the default scope.
- **Always exclude `blocked` (DLQ) sources**, in every scope: they have no fetched content, empty `summary`/`topics`, and a `reading_link` that ingest already failed to open, so surfacing one as a readable result would mislead. If a query clearly matches a blocked source by `title`/`url`, mention it separately as "in the DLQ — run `kboat-rescue` first", not as a readable hit.
- Filter in this skill by reading each note's frontmatter, not through the Base: the Base cannot reliably test empty dates, but here you read the values directly.

## Procedure

1. Resolve the vault from `OBSIDIAN_VAULT_PATH` in `.env`. Read every `Sources/*.md` frontmatter once.
2. Keep the in-scope notes (default `keep`, or the `--states` union above; always drop `blocked`).
3. Rank by lexical overlap between the query and each note's `title`, `topics`, `summary`, and `url`. `topics` and `title` are the strongest signals; `summary` adds recall; a bare URL match is weak. A cheap subagent can read the top candidates and judge relevance against the question when the query is fuzzy.
4. Return the best matches, each with: `title`, `summary`, `source_type`, `reading_link`, and `url`. Order by relevance. If nothing clears the bar in scope, say so and offer to widen via `--states` (e.g. add `distill` or `active`).

## What the user does with a result

- **Just read it:** open `reading_link` — a web URL, or for a PDF the Obsidian/PDF++ link to `PDFs/<slug>.pdf`. No notebook is needed to read.
- **Chat with it or distil it:** decide by the result's `notebooklm_id`, not its disposition. If `notebooklm_id` is present (always for a `keep` source, and for anything whose notebook has not been discarded), open `gemini_url` to chat or check `distill` to have the routine distil it. If it is empty (a distilled `distill`-only source, or a `dismiss`ed one), the notebook is gone — reactivate it first via kboat-notes "Procedure: reactivate a discarded source's notebook".

## Limitations

- Lexical search only. A source whose `summary`/`topics` are empty (the source guide failed at ingest) is findable only by `title`/`url`.
- Semantic / vector search over `summary` is a future enhancement; today this is keyword overlap.
