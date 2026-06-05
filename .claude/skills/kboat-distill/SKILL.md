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

1. Run `.venv/bin/notebooklm auth refresh`. If auth is unusable afterward, STOP and report rather than processing.
2. Probe Basic Memory once with `search_notes(project="k-boat-knowledge", …)`.
   - If the `k-boat-knowledge` project does not exist, the knowledge layer is not set up: **STOP the whole run** and report (create the project first; see README). Phase B's notebook discards are destructive, so do not run them before the durable store exists.
   - If the project exists but the call fails (Basic Memory runtime down), run Phase A but **skip Phase B and Phase C entirely** and report it. Phase B's only destructive act is discarding notebooks, and Phase C only writes concept notes (which needs Basic Memory anyway); deferring both to a healthy day loses nothing, since ingest still runs.
3. Run the deterministic lifecycle pass: `.venv/bin/kboat-lifecycle` (it reads `OBSIDIAN_VAULT_PATH`). This single tool does the whole mechanical core — what used to be hand-evaluated frontmatter logic — so the model never reads every note or does the date math:
   - It **maintains the cooldown clock on disk (Phase A)**: stamps `filed_date` with today's date on newly-dispositioned sources, clears it where every disposition was unchecked. These are the only writes it makes; they are non-destructive, which is why this runs even when Phase B will be skipped. (Pass `--dry-run` to compute without writing — for inspection only.)
   - It **prints the work sets as JSON** on stdout: `phase_a.stamped`/`phase_a.cleared`, `ambiguous`, `phase_b.ripe`, `phase_b.dismiss_discard`, `kindles.ripe`, plus `counts` and `anomalies` (notes that failed to parse or are not the expected `type`). Each source entry carries `slug`, `path`, `title`, `source_type`, `url`, the disposition flags, `filed_date`, `distilled_date`, and `notebooklm_id`. Each Kindle entry carries `slug` (the bare ASIN — the note's filename), `path`, `title`, and `distilled_date`.
   - It **excludes `blocked` (DLQ) sources from both phases**: a blocked source is a DLQ entry with no notebook (awaiting `kboat-rescue`), so it is never stamped, flagged, or listed even if a human checked a disposition on it by mistake.

   Parse this JSON; it is the work list for the rest of the run. The predicates it implements (ripe, dismiss, ambiguous, the 7-day cooldown) are specified in kboat-notes — the tool is an implementation of that spec, not a second source of truth. If the tool is unavailable, fall back to evaluating those predicates by hand over `Sources/*.md`.

## Phase A: maintain the cooldown clock

`.venv/bin/kboat-lifecycle` (preamble step 3) has already done Phase A — the only non-destructive phase — applying both directions of the clock and computing the ambiguous set:

- A source with any disposition (`distill`/`keep`/`dismiss`) checked and `filed_date` empty was **stamped** with today's date — this starts the 7-day cooldown that Phase B counts from. The stamped entries are in `phase_a.stamped`.
- A source with every disposition unchecked and `filed_date` set was **cleared** — the human pulled it back to the active list, so the cooldown is abandoned and Phase B leaves it alone. These are in `phase_a.cleared`.
- Every **ambiguous** source (`dismiss && (keep || distill)`) is in `ambiguous`, regardless of `filed_date`. Ambiguity is a contradiction the routine never processes. Surface every entry of `ambiguous` in the run summary so the human sees it within a day instead of after the 7-day cooldown; make no change to it (it also shows in the Ambiguous Base view).

Because the tool runs every day — even when Basic Memory is down (project present but runtime unavailable; see the preamble) — the clock keeps advancing regardless of Phase B. (Blocked sources are already excluded from both phases; see the preamble.)

## Phase B: act after the cooldown

Phase B runs only when Basic Memory is healthy (see preamble). The tool has already resolved the disposition branching — handling flag co-occurrence, the cooldown, and the ambiguity precedence — so the agent acts directly on its two destructive work sets and nothing else:

- `phase_b.dismiss_discard` — sources `dismiss`ed (alone), past the cooldown, whose notebook is still present. For each, discard the notebook (see kboat-notes "discard a source's notebook") and record it in the report as a dismissed discard. The note and any PDF stay as a de-dup tombstone, excluded from recall. (Dismissed sources whose `notebooklm_id` was already empty are not listed — the discard is idempotent — they are only counted, as `dismiss_already_discarded`.)
- `phase_b.ripe` — sources marked `distill` (and not `dismiss`), past the cooldown, not yet distilled. Process each with the ordered steps below. Each entry carries its `keep` flag: if `keep` is set, retain the notebook at the end instead of discarding it.

Sources that needed no destructive action — ambiguous (`ambiguous`), `keep`-only (`counts.keep_noop`), already distilled (`counts.already_distilled`), or still inside the cooldown (`counts.awaiting_cooldown`) — are not in either set; surface their counts in the summary. The ripe predicate the tool applied is `distill && !dismiss && !blocked && filed_date <= today - 7 days && distilled_date` empty.

Process each `phase_b.ripe` source in this exact order. The order is what makes a crash safe: nothing the notebook holds is destroyed before it is durably recorded, and the `distilled_date` stamp is the commit point.

1. **Resolve the notebook.** Take `notebooklm_id` from the ripe entry (the tool read it from the source note). Run `.venv/bin/notebooklm --quiet list --json`; if the id is absent (the notebook was deleted out of band), record it as an anomaly in the report and skip this source without stamping or discarding. Do not stamp `distilled_date` — nothing was distilled, and stamping it would falsely read as distilled. The source stays ripe and is re-surfaced each run until a human resolves it (e.g. clears the source note); this is the same contract as an extraction error in step 3.
2. **Resolve the source id.** Run `.venv/bin/notebooklm --quiet source list --notebook <notebooklm_id> --json`. With 1:1 there is exactly one source; take its id. Sanity-check it against the note: for a `web_page`, the source `url` matches the note's `url`; for a `pdf`, the source `url` is `null` and its `title` matches the note's `title` (ingest set `--title` for exactly this). `fulltext` is keyed by source id.
3. **Extract** (read-only, safe to repeat). On any extraction error, abort this source — do not stamp, do not discard — record the error and continue to the next source.
   - **Content**: write the full text to a temp file with `.venv/bin/notebooklm --quiet source fulltext <source_id> --notebook <notebooklm_id> -o <tmpfile>`, then read it. Use `-o`, not stdout (which truncates at 2000 chars); avoid `-f markdown` (it needs the `markdownify` package, which is not installed, so it errors out). Confirm the file is a successful fetch (see kboat-notes: the real content — for a web page not empty or a wall, for a PDF not empty or garbled extraction); if not, abort this source as a fetch failure and report it.
   - `.venv/bin/notebooklm --quiet history --notebook <notebooklm_id> --json` — the reading-time dialogue. The dialogue happens through the Gemini UI, which grounds answers in the notebook source but **also draws on web and world knowledge**, citing the sources it used. Keep those citations: a cited claim is source-grounded, an uncited one is external, and the accretion policy treats them differently.
   - `.venv/bin/notebooklm --quiet summary --notebook <notebooklm_id>` — NotebookLM's own summary (text only; no `--json`).
4. **Review material** (optional, best-effort). `.venv/bin/notebooklm --quiet generate flashcards --wait --json --notebook <notebooklm_id>` and/or `generate quiz`. These are async, so `--wait` is required. On timeout or error, mark them "pending" in the report and continue — do not abort the source.
5. **Distill into Basic Memory** following the accretion policy below.
6. **Write the review report** section for this source into `Reviews/YYYY-MM-DD.md` in the vault. Written before the discard, so the extracted material survives even if the discard fails.
7. **Stamp `distilled_date`** with today's date on the source note. This is the commit point; after it the source leaves the ripe set.
8. **Discard the notebook** (see kboat-notes) — **unless `keep` is also set**, in which case retain it and record the retention in the report instead. When discarding, always last: if it fails, the source is already distilled and the report is written — record "notebook discard failed" as a cleanup item for a later pass to reconcile.

A crash anywhere in 1–6 leaves the source ripe and replayable. A crash between 7 and 8 leaves a notebook to clean up later, never lost data.

## Phase C: distil Kindle books

Kindle books are distilled from the highlights in their note body, not from a notebook. Like Phase B, Phase C runs **only when Basic Memory is healthy** (see preamble) — its `write_note`/`edit_note` calls are the only persistent effect, and there is nothing destructive to gate (no notebook, no cooldown). The work set is `kindles.ripe` from the tool: Kindle notes marked `distill` with `distilled_date` empty (see kboat-notes "Kindle note"). Each entry carries `slug` (the bare ASIN — the note's filename), `path`, `title`, and `distilled_date`.

Process each `kindles.ripe` entry in this order — the same crash-safety logic as Phase B, minus the notebook steps (the `distilled_date` stamp is the commit point, and there is no discard):

1. **Read the body.** Read the note at `path` and take everything after the frontmatter — the reading highlights and notes. If the body has **no extractable text** — empty, or only image embeds (`![[…]]`) / whitespace, with no prose to distil — this book cannot be distilled: record it in the report as "skipped (no extractable highlights)" and **do not stamp `distilled_date`**, so it re-surfaces on a later run once the body is filled. (An image-only body is non-empty but carries no text, so it must be skipped too, not distilled to nothing.) Continue to the next book.
2. **Distill into Basic Memory** following the accretion policy below (`project="k-boat-knowledge"`). Grounding for a Kindle book: passages quoted from the book are `#grounded`; the reader's own commentary or interpretation in the body is external, so give it the same `#dialogue` treatment as a source's dialogue claims (double-check before keeping). Provenance is the ASIN, not a URL: `- [source] <title> — ASIN:<asin>`, where `<asin>` is the entry's `slug` (the bare ASIN — the note's filename).
3. **Write the review report** section for this book into `Reviews/YYYY-MM-DD.md` (the same per-run file as Phase B), before the stamp.
4. **Stamp `distilled_date`** with today's date on the Kindle note. This is the commit point; after it the book leaves the ripe set. There is no notebook to discard.

A crash anywhere in 1–3 leaves the book ripe and replayable; the idempotency rules in the accretion policy (skip a provenance observation whose ASIN is already present) keep a replay from double-writing.

## Accretion policy (unattended)

The `memory-ingest` skill assumes a human approves each new entity before it is created. This pass is unattended, so the approval gate becomes an **after-the-fact review gate**: every create, append, and skip decision is logged in the report for the human to reverse later.
Every Basic Memory call passes `project="k-boat-knowledge"` (see the top of this skill).

- **Append first.** Before creating a concept note, `search_notes` with at least three query variations: the exact title, a paraphrase, and the key English term if the concept has one. The project's embedding model is English (`bge-small`), so semantic recall on Japanese titles is weak — lean on these lexical variations. If a hit clears the bar, add to the existing note instead of creating a duplicate, with `edit_note(operation="insert_after_section", section="## Observations")` (or `"## Relations"`); plain `append` lands past the sections, so use `insert_after_section`. See `memory-curate`.
- **Create only specific concepts.** Auto-create a standalone note only for a clearly named concept (an algorithm, system, protocol, paper). For vague or broad concepts, do not create a note; log it as an "uncreated candidate" for the human to promote.
- **Cap creates per run.** Set a hard ceiling on new concept notes per run. If hit, stop creating, finish appends, and escalate in the report.
- **Ground every claim.** Treat `source fulltext` (and the source-grounded NotebookLM `summary`) as the authority. Tag each distilled observation by grounding: `#grounded` when the source supports it, `#dialogue` when it is external knowledge the conversation brought in (an uncited Gemini answer). Never let a `#dialogue` claim read as if it came from the source.
- **Double-check dialogue-derived claims.** A `#dialogue` claim is not source-grounded, so before keeping it, try to refute it against the source and your own knowledge. If it survives, accret it tagged `#dialogue`; if it fails or you are uncertain, do not accret it — log it as an uncreated candidate in the report. This keeps the genuinely useful outside knowledge the dialogue surfaced (the reason to chat while reading) while dropping the unverifiable — the safeguard for the lower-grounding Gemini-Flash dialogue.
- **Always record provenance.** Each contributing source adds, once per concept note, a provenance observation `- [source] <title> — <url>` carrying the source's canonical URL (for a Kindle book, `- [source] <title> — ASIN:<asin>`, where `<asin>` is the entry's `slug` — the bare ASIN, i.e. the note's filename) — it records that this source fed the concept, regardless of grounding; the per-claim `#grounded`/`#dialogue` tag records whether the claim came from the source or from external dialogue knowledge. The source/Kindle note lives in the vault, a separate root, so a wikilink could not reach it; the URL or ASIN is root-independent and keeps each autonomous decision traceable. (Relations *between concepts* stay wikilinks; both ends are in this root.)
- **Never auto-merge.** Merging concept notes is destructive and hard to reverse unattended. Log merge candidates in the report for `memory-curate` to handle with a human.
- **Stay idempotent on replay.** The project's `write_note` does not overwrite by default, so never issue a second `write_note` for the same concept — use `edit_note` with `insert_after_section`. Before inserting, check the section text and skip a provenance observation whose URL (or, for a Kindle book, ASIN) is already present, so a replay after a mid-run crash does not double-write.

## Review report (`Reviews/YYYY-MM-DD.md`)

One file per run, in the vault. For each distilled source, a decision log:

- `created:` new concept notes.
- `appended-to:` existing concept notes that grew.
- `kept from dialogue:` external (`#dialogue`) claims that passed the double-check.
- `skipped (dup of):` observations dropped as duplicates.
- `uncreated candidates:` concepts left for the human to promote — including `#dialogue` claims that failed the double-check or were uncertain.
- `merge candidates:` pairs flagged for `memory-curate`.

Plus the source's `summary` verbatim and any flashcards/quiz (or "pending"), the dismissed discards (sources whose notebook was discarded without distilling) and notebook retentions (distilled sources kept under `keep`), and an anomalies section (notebook missing, discard failed, extraction errors, ambiguous dispositions left unprocessed).

For each distilled **Kindle book** (Phase C), the same decision log (`created`/`appended-to`/`kept from dialogue`/`skipped (dup of)`/`uncreated candidates`/`merge candidates`), and the Kindle books skipped for no extractable highlights (left ripe for a later run). Kindle books have no notebook, summary, or flashcards.

## Run summary

End the run with counts — most come straight from the tool's `counts` block (Phase A: `filed_stamped`, `filed_cleared`, `ambiguous`; Phase B: `ripe`, `dismiss_discard`, `keep_noop`, `already_distilled`, `dismiss_already_discarded`, `awaiting_cooldown`; Phase C: `kindles_ripe`, `kindles_already_distilled`, `kindles_total`) — plus what only the agent knows (sources and Kindle books actually distilled, notebooks retained under `keep`, Kindle books skipped for no extractable highlights, items left for the next run by errors). Report the tool's `anomalies` (unparseable, or non-`source`/non-`kindle` notes), whether the run stopped because the `k-boat-knowledge` project was missing or skipped Phase B/C for a Basic Memory outage, and every error with the source or book it affected and the cause.
