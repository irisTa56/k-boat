---
name: kboat-recall
description: Find shelved "read later" sources by a natural-language question. Use when the user asks whether they saved something to read on a topic, wants to surface kept sources matching a question, or says things like "do I have anything about X to read", "find that paper I shelved", "what's on my read-later shelf about Y". Searches source-note title/summary/topics; defers to kboat-notes for the schema.
---

# K-Boat recall

Answer "do I have something saved to read about X?" by searching the source notes.
A K-Boat source that is `done` but not `distill` is **shelved** — kept as a searchable archive entry whose notebook has been discarded (see kboat-notes lifecycle). Its `summary` and `topics`, captured at ingest, are the durable signal this skill searches.

Follow the kboat-notes skill for the source-note schema and the lifecycle. This skill is read-only — it never edits notes or touches NotebookLM.

## Scope

- **Default: the shelf.** Search sources with `done == true && distill != true` (the read-later cold storage). These are the ones the user parked to read later.
- **Widen on request.** If the user asks to search everything (or the shelf yields nothing), include all `Sources/*.md` — active inbox items, in-flight, and distilled — not just the shelf.
- Filter in this skill by reading each note's frontmatter, not through the Base: the Base cannot reliably test empty dates, but here you read the values directly.

## Procedure

1. Resolve the vault from `OBSIDIAN_VAULT_PATH` in `.env`. Read every `Sources/*.md` frontmatter once.
2. Keep the in-scope notes (default shelf predicate above).
3. Rank by lexical overlap between the query and each note's `title`, `topics`, `summary`, and `url`. `topics` and `title` are the strongest signals; `summary` adds recall; a bare URL match is weak. A cheap subagent can read the top candidates and judge relevance against the question when the query is fuzzy.
4. Return the best matches, each with: `title`, `summary`, `source_type`, `reading_link`, and `url`. Order by relevance. If nothing clears the bar on the shelf, say so and offer to widen to all sources.

## What the user does with a result

- **Just read it:** open `reading_link` — a web URL, or for a PDF the Obsidian/PDF++ link to `PDFs/<slug>.pdf`. No notebook is needed to read.
- **Chat with it or distil it:** the shelved source has no notebook. Re-create one with kboat-notes "Procedure: reactivate a shelved source's notebook", then check `distill` to have the routine distil it.

## Limitations

- Lexical search only. A source whose `summary`/`topics` are empty (the source guide failed at ingest) is findable only by `title`/`url`.
- Semantic / vector search over `summary` is a future enhancement; today this is keyword overlap.
