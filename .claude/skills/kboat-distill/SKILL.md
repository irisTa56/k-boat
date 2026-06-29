---
name: kboat-distill
description: Advance filed sources through the K-Boat lifecycle and distill ripe ones into the knowledge graph. Use when running the post-reading pass — stamping filed dates, discarding or retaining notebooks per disposition, distilling sources opted in with distill at least a week ago, and distilling Kindle books marked distill, into Basic Memory concept notes with a review report. Defers to kboat-notes for the source-note and Kindle-note schema and to the Basic Memory skills for the concept-note conventions.
---

# K-Boat distillation

The post-reading pass. It moves source notes through their lifecycle and distills the ripe ones — those marked `distill` at least a week ago — into concept notes that accrete across sources, then discards the throwaway NotebookLM notebook (unless the source is also `keep`, which retains it).
It also distills ripe **Kindle books** — those marked `distill` — from the highlights in their note body (Phase C below); these have no notebook and no cooldown.
It runs unattended after kboat-ingest in the daily routine, and can also be run by hand.

Follow kboat-notes for the source-note schema, the lifecycle state machine, and the discard procedure.
Follow the Basic Memory skills for the concept graph: `memory-notes` (note structure), `memory-ingest` (entity matching), `memory-curate` (merging).
Every Basic Memory call (`search_notes`, `write_note`, `edit_note`) must pass `project="k-boat-knowledge"` (the project rooted at `KBOAT_KNOWLEDGE_PATH`); without it the server writes to its default project, which is the wrong graph.

## Run-level preamble

Run `eval "$(mise env)"` at the top of every shell block here (see kboat-notes "Environment"): it loads `.env` over the `mise.toml` defaults and puts the venv on `PATH`, so `notebooklm`, `kboat-lifecycle`, and `$OBSIDIAN_VAULT_PATH` resolve bare. The Bash tool keeps no shell state between calls, so re-run it in each block.

1. Run `notebooklm auth refresh`. If auth is unusable afterward, STOP and report rather than processing.
2. Probe Basic Memory once with `search_notes(project="k-boat-knowledge", …)`.
   - If the `k-boat-knowledge` project does not exist, the knowledge layer is not set up: **STOP the whole run** and report (create the project first; see README). Phase B's notebook discards are destructive, so do not run them before the durable store exists.
   - If the project exists but the call fails (Basic Memory runtime down), run Phase A but **skip Phase B and Phase C entirely** and report it. Phase B's only destructive act is discarding notebooks, and Phase C only writes concept notes (which needs Basic Memory anyway); deferring both to a healthy day loses nothing, since ingest still runs.
3. Run the deterministic lifecycle pass: `kboat-lifecycle` (it reads `OBSIDIAN_VAULT_PATH`). This single tool does the whole mechanical core — what used to be hand-evaluated frontmatter logic — so the model never reads every note or does the date math:
   - It **maintains the cooldown clock on disk (Phase A)**: stamps `filed_date` with today's date on newly-dispositioned sources, clears it where every disposition was unchecked. These are the only writes it makes; they are non-destructive, which is why this runs even when Phase B will be skipped. (Pass `--dry-run` to compute without writing — for inspection only.)
   - It **prints the work sets as JSON** on stdout: `phase_a.stamped`/`phase_a.cleared`, `ambiguous`, `phase_b.ripe`, `phase_b.dismiss_discard`, `kindles.ripe`, plus `counts` and `anomalies` (notes that failed to parse or are not the expected `type`). Each source entry carries `slug`, `path`, `title`, `source_type`, `url`, the disposition flags, `filed_date`, `distilled_date`, and `notebooklm_id`. Each Kindle entry carries `slug` (the bare ASIN — the note's filename), `path`, `title`, and `distilled_date`.
   - It **excludes `blocked` (DLQ) sources from both phases**: a blocked source is a DLQ entry with no notebook (awaiting `kboat-rescue`), so it is never stamped, flagged, or listed even if a human checked a disposition on it by mistake.

   Parse this JSON; it is the work list for the rest of the run. The predicates it implements (ripe, dismiss, ambiguous, the 7-day cooldown) are specified in kboat-notes — the tool is an implementation of that spec, not a second source of truth. If the tool is unavailable, fall back to evaluating those predicates by hand over `Sources/*.md`.

## Phase A: maintain the cooldown clock

`kboat-lifecycle` (preamble step 3) has already done Phase A — the only non-destructive phase — applying both directions of the clock and computing the ambiguous set:

- A source with any disposition (`distill`/`keep`/`dismiss`) checked and `filed_date` empty was **stamped** with today's date — this starts the 7-day cooldown that Phase B counts from. The stamped entries are in `phase_a.stamped`.
- A source with every disposition unchecked and `filed_date` set was **cleared** — the human pulled it back to the active list, so the cooldown is abandoned and Phase B leaves it alone. These are in `phase_a.cleared`.
- Every **ambiguous** source (`dismiss && (keep || distill)`) is in `ambiguous`, regardless of `filed_date`. Ambiguity is a contradiction the routine never processes. Surface every entry of `ambiguous` in the run summary so the human sees it within a day instead of after the 7-day cooldown; make no change to it (it also shows in the Ambiguous Base view).

Because the tool runs every day — even when Basic Memory is down (project present but runtime unavailable; see the preamble) — the clock keeps advancing regardless of Phase B. (Blocked sources are already excluded from both phases; see the preamble.)

## Phase B: act after the cooldown

Phase B runs only when Basic Memory is healthy (see preamble). The tool has already resolved the disposition branching — handling flag co-occurrence, the cooldown, and the ambiguity precedence — so the agent acts directly on its two destructive work sets and nothing else:

- `phase_b.dismiss_discard` — sources `dismiss`ed (alone), past the cooldown, whose notebook is still present. For each, discard the notebook (see kboat-notes "discard a source's notebook") and record it in the run summary as a dismissed discard. The note and any PDF stay as a de-dup tombstone, excluded from recall. (Dismissed sources whose `notebooklm_id` was already empty are not listed — the discard is idempotent — they are only counted, as `dismiss_already_discarded`.)
- `phase_b.ripe` — sources marked `distill` (and not `dismiss`), past the cooldown, not yet distilled. Process each with the ordered steps below. Each entry carries its `keep` flag: if `keep` is set, retain the notebook at the end instead of discarding it.

Sources that needed no destructive action — ambiguous (`ambiguous`), `keep`-only (`counts.keep_noop`), already distilled (`counts.already_distilled`), or still inside the cooldown (`counts.awaiting_cooldown`) — are not in either set; surface their counts in the summary. The ripe predicate the tool applied is `distill && !dismiss && !blocked && filed_date <= today - 7 days && distilled_date` empty.

Process each `phase_b.ripe` source in this exact order. The order is what makes a crash safe: nothing the notebook holds is destroyed before it is durably recorded, and the `distilled_date` stamp is the commit point.

1. **Resolve the notebook.** Take `notebooklm_id` from the ripe entry (the tool read it from the source note). Run `notebooklm --quiet list --json`; if the id is absent (the notebook was deleted out of band), record it as an anomaly in the run summary and skip this source without stamping or discarding. Do not stamp `distilled_date` — nothing was distilled, and stamping it would falsely read as distilled. The source stays ripe and is re-surfaced each run until a human resolves it (e.g. clears the source note); this is the same contract as an original-source extraction error in step 3.
2. **Resolve the sources.** Run `notebooklm --quiet source list --notebook <notebooklm_id> --json`. The notebook holds the **original** source plus any reading-time dialogue saved back as a NotebookLM note — each saved note is an additional source (usually `url: null`, a note / "unknown" type, with a non-original `title`), which is expected, not a 1:1 violation (see kboat-notes "Saved dialogue as extra sources"). Identify the original: for a `web_page`, the source whose `url` matches the note's `url`; for a `pdf`, the source with `url: null` whose `title` matches the note's `title` (ingest renames the source to the note's `title` after upload for exactly this — a file upload's `--title` does not stick; see kboat-notes "One notebook per source"). Take its id as the grounded authority, and treat **every other source as saved dialogue** to extract in step 3. `fulltext` is keyed by source id.
3. **Extract** (read-only, safe to repeat). Only an extraction error on the **original** source aborts this source — do not stamp, do not discard; record the error in the run summary and continue to the next source. Errors on the other extractions below — saved dialogue notes, `history`, `summary` — are non-fatal: record them in the run summary and continue, do not abort.
   - **Original content (grounded)**: write the full text of the **original** source (the id resolved in step 2) to a temp file with `notebooklm --quiet source fulltext <source_id> --notebook <notebooklm_id> -o <tmpfile>`, then read it. Use `-o`, not stdout (which truncates at 2000 chars); avoid `-f markdown` (it needs the `markdownify` package, which is not installed, so it errors out). Confirm the file is a successful fetch (see kboat-notes: the real content — for a web page not empty or a wall, for a PDF not empty or garbled extraction); if not, abort this source as a fetch failure and report it in the run summary. This is the `#grounded` authority.
   - **Saved dialogue notes (dialogue)**: for each *other* source from step 2 (reading-time dialogue you saved into the notebook), `fulltext` it the same way and read it. Its content is dialogue, not the source — treat every claim as `#dialogue`: vet it per the accretion policy before keeping, and key its provenance to the **original** source's `url`. Skip any note that won't extract and report it in the run summary (non-fatal, per the opener). There may be zero such notes.
   - `notebooklm --quiet history --notebook <notebooklm_id> --json` — reading-time dialogue left in the chat (may be empty when you saved it as notes instead). The dialogue happens through the Gemini UI, which grounds answers in the notebook source but **also draws on web and world knowledge**, citing the sources it used. Keep those citations: a cited claim is source-grounded, an uncited one is external, and the accretion policy treats them differently.
   - `notebooklm --quiet summary --notebook <notebooklm_id>` — NotebookLM's own summary (text only; no `--json`).
4. **Review material** (optional, best-effort). `notebooklm --quiet generate flashcards --wait --json --notebook <notebooklm_id>` and/or `generate quiz`. These are async, so `--wait` is required. On timeout or error, mark them `pending` in the report and continue — do not abort the source. If you skip this step entirely (it is optional), record `skipped` instead.
5. **Distill into Basic Memory** following the accretion policy below.
6. **Write the review report** section for this source into `Reviews/YYYY-MM-DD.md` in the vault. Written before the discard, so the extracted material survives even if the discard fails.
7. **Stamp `distilled_date`** with today's date on the source note. This is the commit point; after it the source leaves the ripe set.
8. **Discard the notebook** (see kboat-notes) — **unless `keep` is also set**, in which case retain it and note the retention in the run summary instead. When discarding, always last: if it fails, the source is already distilled and the report is written — record "notebook discard failed" in the run summary as a cleanup item for a later pass to reconcile.

A crash anywhere in 1–6 leaves the source ripe and replayable. A crash between 7 and 8 leaves a notebook to clean up later, never lost data.

## Phase C: distil Kindle books

Kindle books are distilled from the highlights in their note body, not from a notebook. Like Phase B, Phase C runs **only when Basic Memory is healthy** (see preamble) — its `write_note`/`edit_note` calls are the only persistent effect, and there is nothing destructive to gate (no notebook, no cooldown). The work set is `kindles.ripe` from the tool: Kindle notes marked `distill` with `distilled_date` empty (see kboat-notes "Kindle note"). Each entry carries `slug` (the bare ASIN — the note's filename), `path`, `title`, and `distilled_date`.

Process each `kindles.ripe` entry in this order — the same crash-safety logic as Phase B, minus the notebook steps (the `distilled_date` stamp is the commit point, and there is no discard):

1. **Read the body.** Read the note at `path` and take everything after the frontmatter — the reading highlights and notes. If the body has **no extractable text** — empty, or only image embeds (`![[…]]`) / whitespace, with no prose to distil — this book cannot be distilled: record it in the run summary as "skipped (no extractable highlights)" and **do not stamp `distilled_date`**, so it re-surfaces on a later run once the body is filled. (An image-only body is non-empty but carries no text, so it must be skipped too, not distilled to nothing.) Continue to the next book.
2. **Distill into Basic Memory** following the accretion policy below (`project="k-boat-knowledge"`). Grounding for a Kindle book: passages quoted from the book are `#grounded`; the reader's own commentary or interpretation in the body is external, so give it the same `#dialogue` treatment as a source's dialogue claims (double-check before keeping). Provenance is the ASIN, not a URL: `- [source] <title> — ASIN:<asin>`, where `<asin>` is the entry's `slug` (the bare ASIN — the note's filename).
3. **Write the review report** section for this book into `Reviews/YYYY-MM-DD.md` (the same per-run file as Phase B), before the stamp.
4. **Stamp `distilled_date`** with today's date on the Kindle note. This is the commit point; after it the book leaves the ripe set. There is no notebook to discard.

A crash anywhere in 1–3 leaves the book ripe and replayable; the idempotency rules in the accretion policy (skip a provenance observation whose ASIN is already present) keep a replay from double-writing.

## Accretion policy (unattended)

The `memory-ingest` skill assumes a human approves each new entity before it is created. This pass is unattended, so the approval gate becomes an **after-the-fact review gate**: every create, append, and skip decision is logged in the report for the human to reverse later.
Every Basic Memory call passes `project="k-boat-knowledge"` (see the top of this skill).

- **Append first.** Before creating a concept note, `search_notes` with at least three query variations: the exact title, a paraphrase, and the key English term if the concept has one. The project's embedding model is English (`bge-small`), so semantic recall on Japanese titles is weak — lean on these lexical variations. If a hit clears the bar, add to the existing note instead of creating a duplicate, with `edit_note(operation="insert_after_section", section="## Observations")` (or `"## Relations"`); plain `append` lands past the sections, so use `insert_after_section`. See `memory-curate`.
- **Create only specific concepts.** Auto-create a standalone note only for a clearly named concept (an algorithm, system, protocol, paper). For vague or broad concepts, do not create a note; log it as an "uncreated candidate" for the human to promote.
- **Cap creates per run.** Set a hard ceiling on new concept notes per run. If hit, stop creating, finish appends, log the deferred concepts as uncreated candidates in the report (they are knowledge to promote), and escalate the cap-hit itself — that the ceiling was reached and how many were left — in the run summary.
- **Ground every claim.** Treat the **original** source's `fulltext` (and the source-grounded NotebookLM `summary`) as the authority. Tag each distilled observation by grounding: `#grounded` when the original source supports it, `#dialogue` when it is external knowledge the conversation brought in — a saved dialogue note (an extra notebook source, see Extract) or an uncited Gemini answer in `history`. Never let a `#dialogue` claim read as if it came from the source.
- **Double-check dialogue-derived claims.** A `#dialogue` claim is not source-grounded, so before keeping it, try to refute it against the source and your own knowledge. If it survives, accret it tagged `#dialogue`; if it fails or you are uncertain, do not accret it — log it as an uncreated candidate in the report. This keeps the genuinely useful outside knowledge the dialogue surfaced (the reason to chat while reading) while dropping the unverifiable — the safeguard for the lower-grounding Gemini-Flash dialogue.
- **Always record provenance.** Each contributing source adds, once per concept note, a provenance observation `- [source] <title> — <url>` carrying the source's canonical URL (for a Kindle book, `- [source] <title> — ASIN:<asin>`, where `<asin>` is the entry's `slug` — the bare ASIN, i.e. the note's filename) — it records that this source fed the concept, regardless of grounding; the per-claim `#grounded`/`#dialogue` tag records whether the claim came from the source or from external dialogue knowledge (a claim from a saved dialogue note shares the original source's URL — it gets no separate provenance, since the dialogue happened over that source). The source/Kindle note lives in the vault, a separate root, so a wikilink could not reach it; the URL or ASIN is root-independent and keeps each autonomous decision traceable. (Relations *between concepts* stay wikilinks; both ends are in this root.)
- **Reuse facet tags from the vocabulary.** A concept note's frontmatter facet tags come from the canonical set in the `meta/Tag vocabulary` note (`memory://k-boat-knowledge/meta/tag-vocabulary`); read it first, reuse an existing tag where one fits, and mint a new tag only when none does — adding it to that note under the right family in the same change. This keeps the tag set from drifting into near-duplicate variants (for example `inference` vs `llm-inference`).
- **Mark up formula notation.** When an observation states a formula, equation, or named quantity (not a symbol mentioned in prose), wrap it per kboat-notes "Math and formula notation": code for an ASCII-faithful expression, `$`/`$$` for notation needing math typography, defaulting to code. This keeps the boundary of what is a formula explicit rather than leaving a bare expression to read as running text.
- **Never auto-merge.** Merging concept notes is destructive and hard to reverse unattended. Log merge candidates in the report for `memory-curate` to handle with a human.
- **Stay idempotent on replay.** The project's `write_note` does not overwrite by default, so never issue a second `write_note` for the same concept — use `edit_note` with `insert_after_section`. Before inserting, check the section text and skip a provenance observation whose URL (or, for a Kindle book, ASIN) is already present, so a replay after a mid-run crash does not double-write.

## Review report (`Reviews/YYYY-MM-DD.md`)

The review report is the durable, **user-facing** record of what each distillation taught — read for memory consolidation, not a run log.
So it carries **only the distillation knowledge** below, and is **written only on a run that distilled at least one source or Kindle book** (Phase B/C).
A run that distilled nothing writes no report: there is nothing to consolidate, and its operational outcome (the Phase A lifecycle counts, dismissed discards and notebook retentions, anomalies, the "nothing ripe" status) lives in the run summary, not the vault.

The first write of the run **creates the file with its frontmatter block** (see kboat-notes "Review note"), then appends the first `###` section; later writes in the same run append further sections only.

```yaml
---
type: review
date: <YYYY-MM-DD>
read: false
---
```

The block is **mandatory**: `type: review` is what `Reviews.base` filters on, so a report written without it drops out of the Base entirely. `read: false` is the human's read-tracking flag for the Base's Unread view; set `date` to the run date (the same as the filename). On a **replay** where the file already exists (a crash left it after the first section was written), append sections only — never rewrite the frontmatter block, so a `read: true` the reader has since toggled is never clobbered.

Each distilled source (and Kindle book) gets its own `###` section under the report, laid out for scanning — a one-line reference to the original, then a bulleted **Summary**, then the **Basic Memory Report** (the decision log):

```markdown
### {title}

Source: <url> (for a Kindle book: ASIN:<asin>)

#### Summary

- a bulleted consolidation, in the same language as the source `summary` (Japanese)
- synthesise the original's key points **and the verified dialogue takeaways** (the `kept from dialogue` claims) — the reader's new or noteworthy findings often live in the dialogue, so weave those in rather than copying the source `summary` verbatim (it already lives on the source note)
- one point per bullet, and mark a dialogue-derived bullet (a trailing `#dialogue`) so it never reads as if the source stated it

#### Basic Memory Report

- `created:` new concept notes.
- `appended-to:` existing concept notes that grew.
- `kept from dialogue:` external (`#dialogue`) claims that passed the double-check.
- `skipped (dup of):` observations dropped as duplicates.
- `uncreated candidates:` concepts left for the human to promote — including `#dialogue` claims that failed the double-check or were uncertain.
- `merge candidates:` pairs flagged for `memory-curate`.
- `flashcards/quiz:` the generated review material, `pending` (attempted but not ready), or `skipped` (the optional step was skipped).
```

Write the Basic Memory Report values in the **same language as the Summary** (Japanese prose), keeping concept-note names and established English terms (`recompute`, FLOP, OCS, …) in English (the backtick keys are fixed literals, never translated). Each key holds **one line**, with `; ` as the top-level item separator so a `、` inside a clause is never read as an item boundary:

- `created:` — the new concept-note names as plain text, never `[[wikilinks]]` (they can't resolve from `Reviews/` in the vault root to the concept notes in the separate `KBOAT_KNOWLEDGE_PATH` root, the same reason provenance uses a URL).
- `appended-to:` — the concept-note names that grew (same plain-text rule), each optionally followed by a `（…）` note of what was added.
- `kept from dialogue:`, `uncreated candidates:`, `skipped (dup of):`, `merge candidates:` — short, self-contained Japanese phrases.
- `flashcards/quiz:` — its own status (the generated material / `pending` / `skipped`), never `none`.

Write `none` for any of these keys except `flashcards/quiz:` when there is nothing to report. Annotate a per-run create-cap hit **once**: suffix `created:` with `(N created, create cap reached)`, and mark each concept it deferred under `uncreated candidates:` with a uniform `deferred (create cap reached)` rather than restating the count.

A **Kindle book** (Phase C) uses the same two-subsection layout, with `ASIN:<asin>` as the reference and its Summary drawn from the book and the reader's own `#dialogue` commentary in the body. Kindle books have no notebook, so they omit the `flashcards/quiz:` line.

Everything operational stays out of the report and goes to the run summary only — the Run summary section below is the authoritative list of what to report there.

## Run summary

End the run with counts — most come straight from the tool's `counts` block (Phase A: `filed_stamped`, `filed_cleared`, `ambiguous`; Phase B: `ripe`, `dismiss_discard`, `keep_noop`, `already_distilled`, `dismiss_already_discarded`, `awaiting_cooldown`; Phase C: `kindles_ripe`, `kindles_already_distilled`, `kindles_total`) — plus what only the agent knows (sources and Kindle books actually distilled, the dismissed discards, notebooks retained under `keep`, Kindle books skipped for no extractable highlights, concepts left uncreated because the per-run create cap was hit, ambiguous dispositions left unprocessed, and items left for the next run by errors). Report the tool's `anomalies` (unparseable, or non-`source`/non-`kindle` notes), the per-source/Kindle anomalies the agent hit (notebook missing, discard failed, an original-source extraction/fetch error, and non-fatal errors on a saved dialogue note, `history`, or `summary`), whether the run stopped because the `k-boat-knowledge` project was missing or skipped Phase B/C for a Basic Memory outage, and every error with the source or book it affected and the cause. The run summary is the **sole** home for this operational detail — the review report carries the distillation knowledge only (see "Review report"), so a run that distilled nothing reports here and writes no report.
