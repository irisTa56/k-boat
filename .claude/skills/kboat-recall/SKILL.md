---
name: kboat-recall
description: Find shelved "read later" sources by a natural-language question, and run the routine's daily pick. Use when the user asks whether they saved something to read on a topic, wants to surface kept sources matching a question, or says things like "do I have anything about X to read", "find that paper I shelved", "what's on my read-later shelf about Y" — and when the kboat-routine runs the daily pick after distillation (the write-capable "Daily pick mode" below: surface up to two web sources for today by inferring the reader's interests from their open-questions backlog and their recent Daily notes, via the kboat-pick package). Searches source-note title/summary/topics; defers to kboat-notes for the schema.
---

# K-Boat recall

Answer "do I have something saved to read about X?" by searching the source notes.
A K-Boat source marked `keep` is the **read-later shelf** — kept as a searchable archive entry whose notebook is retained for re-reading and dialogue (see kboat-notes lifecycle). Its `summary` and `topics`, captured at ingest, are the durable signal this skill searches.

Follow the kboat-notes skill for the source-note schema and the lifecycle. The search use is read-only; the "Daily pick mode" below is the one exception — it writes only the `picked` flag, through `kboat-pick`. Neither use touches NotebookLM.

## Scope

- **Default: `keep`.** Search sources with `keep == true` — the read-later shelf the user chose to hold (this covers `keep` alone and `keep` + `distill`). These are the ones parked to read later.
- **Specify the states.** A `--states <list>` argument redirects the scope to the union of the named dispositions, drawn from `keep`, `distill`, `active`, `dismiss` (comma-separated, e.g. `--states keep,distill`). `active` is the undispositioned inbox, computed as `!distill && !keep && !dismiss && !blocked` — there is no `active` property, so derive it from the flags. Include `dismiss` only when the user names it explicitly: it is the abandoned pile, deliberately out of the default scope.
- **Always exclude `blocked` (DLQ) sources**, in every scope: they have no fetched content, empty `summary`/`topics`, and a `reading_link` that ingest already failed to open, so surfacing one as a readable result would mislead. If a query clearly matches a blocked source by `title`/`url`, mention it separately as "in the DLQ — run `kboat-rescue` first", not as a readable hit.
- Filter in this skill by reading each note's frontmatter, not through the Base: the Base cannot reliably test empty dates, but here you read the values directly.

## Procedure

1. Load the env with `eval "$(mise env)"` so `$OBSIDIAN_VAULT_PATH` is set from `.env` (see kboat-notes "Environment"). Read every `Sources/*.md` frontmatter once.
2. Keep the in-scope notes (default `keep`, or the `--states` union above; always drop `blocked`).
3. Rank by lexical overlap between the query and each note's `title`, `topics`, `summary`, and `url`. `topics` and `title` are the strongest signals; `summary` adds recall; a bare URL match is weak. A cheap subagent can read the top candidates and judge relevance against the question when the query is fuzzy.
4. Return the best matches, each with: `title`, `summary`, `source_type`, `reading_link`, and `url`. Order by relevance. If nothing clears the bar in scope, say so and offer to widen via `--states` (e.g. add `distill` or `active`).

## What the user does with a result

- **Just read it:** open `reading_link` — a web URL, or for a PDF the Obsidian/PDF++ link to `PDFs/<slug>.pdf`. No notebook is needed to read.
- **Chat with it or distil it:** decide by the result's `notebooklm_id`, not its disposition. If `notebooklm_id` is present (always for a `keep` source, and for anything whose notebook has not been discarded), open `gemini_url` to chat or check `distill` to have the routine distil it. If it is empty (a distilled `distill`-only source, or a `dismiss`ed one), the notebook is gone — reactivate it first via kboat-notes "Procedure: reactivate a discarded source's notebook".

## Daily pick mode

A second, write-capable mode, run by the `kboat-routine` after distillation — not by a human asking a question. It surfaces at most two web sources for today by inferring what the reader is currently interested in from two signals — their open-questions backlog and their recent Daily notes — and is the only part of this skill that edits notes, and only the `picked` flag, via the `kboat-pick` tool. The spec is kboat-notes "Daily pick".

1. `eval "$(mise env)"`, then gather both interest signals:
   - `kboat-pick candidates` → JSON with `daily_notes` (the recent Daily-note bodies, newest-first, within the look-back window — the last two weeks by default; the window used is echoed as `lookback_days`) and `candidates` (the active web inbox, each with `summary`/`topics`).
   - `rem list --list "K-Boat Questions" --incomplete --output json` → the open-questions backlog (unresolved only). Each item's `name` is the question, `notes` may add context, and `priority`/`priority_label` and `flagged` are the priority. An empty backlog — or a list that does not exist yet — comes back as `[]` (exit 0); treat any genuine `rem` failure the same way. Either means no backlog signal this run, so the pick proceeds on the Daily notes alone — or, if there are none either, yields zero picks per step 2.
2. If there are no `candidates`, or there are neither `daily_notes` nor open questions, run `kboat-pick set --slugs ""` to clear any stale `picked`, then stop and report zero picks.
3. **Infer the interests, then rank.** Read the open questions and the `daily_notes` bodies (newest first) and infer what the reader wants to read or learn about now: an open question is the strongest, most deliberate signal; the notes add the topics, problems, and themes they are currently engaging with; ignore logbook noise (done tasks, schedules, unrelated journaling). Then rank the candidates against those interests from their `summary`/`topics` (delegate to a cheap subagent when the inbox, the notes, or the question list are large, lexically pre-filtering first to keep it cheap — a Japanese note leans on the Japanese `summary`, since `topics` is English), in two tiers — see kboat-notes "Daily pick":
   - **Tier 1 — direct interest**: candidates that directly match a current interest (a candidate that answers an open question, or one matching a clear topic in the notes). Accumulate distinct picks until two; order question matches before notes-only matches, and higher-`priority`/`flagged` questions first.
   - **Tier 2 — same-field learning**: only if Tier 1 yields fewer than two, top up with candidates in the broader field or theme of the open questions or the recent notes (not a direct match, but genuinely on-topic) — question fields first, then newest-note-first.
   Stop at two; a candidate is picked at most once (Tier 1 over Tier 2). If even Tier 2 finds nothing in the field of the questions or the notes, take fewer — never pad with an unrelated read (an honest short list is the goal, not a strict miss).
4. `kboat-pick set --slugs <slug1>,<slug2>` (the slugs you chose, or fewer) → resets `picked` on every source and sets it on your choices. Relay its JSON (`picked`, `missing`, `reset`); a non-empty `missing` is a defect to report.
5. Report the picks — each with the interest it matched (the open question, or the dated note), so the inference is visible and checkable — and that they are read in the Today view of the reading-inbox Base (kboat-notes "Reading inbox Base").

This mode never writes the Daily note and never touches NotebookLM. The picks are a spotlight, replaced each run.

## Limitations

- Lexical search only. A source whose `summary`/`topics` are empty (the source guide failed at ingest) is findable only by `title`/`url`.
- Semantic / vector search over `summary` is a future enhancement; today this is keyword overlap.
