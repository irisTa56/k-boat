---
name: kboat-distill
description: Advance read sources through the K-Boat lifecycle and distill ripe ones into the knowledge graph. Use when running the post-reading pass — stamping done dates, discarding notebooks for unread done sources, and distilling sources read at least a week ago into Basic Memory concept notes with a review report. Defers to kboat-notes for the source-note schema and to the Basic Memory skills for the concept-note conventions.
---

# K-Boat distillation

The post-reading pass. It moves source notes through their lifecycle and distills the ripe ones — those read and marked `done` at least a week ago — into concept notes that accrete across sources, then discards the throwaway NotebookLM notebook.
It runs unattended after kboat-ingest in the daily routine, and can also be run by hand.

Follow kboat-notes for the source-note schema, the lifecycle state machine, and the discard procedure.
Follow the Basic Memory skills for the concept graph: `memory-notes` (note structure), `memory-ingest` (entity matching), `memory-curate` (merging).
Every Basic Memory call (`search_notes`, `write_note`, `edit_note`) must pass `project="k-boat-knowledge"` (the project rooted at `KBOAT_KNOWLEDGE_PATH`); without it the server writes to its default project, which is the wrong graph.

## Run-level preamble

1. Run `.venv/bin/notebooklm auth refresh`. If auth is unusable afterward, STOP and report rather than processing.
2. Probe Basic Memory once with `search_notes(project="k-boat-knowledge", …)`.
   - If the `k-boat-knowledge` project does not exist, the knowledge layer is not set up: **STOP the whole run** and report (create the project first; see README). Phase B's notebook discards are destructive, so do not run them before the durable store exists.
   - If the project exists but the call fails (Basic Memory runtime down), run Phase A but **skip Phase B entirely** and report it. Phase B's only destructive act is discarding notebooks; deferring it to a healthy day loses nothing, since ingest still runs.
3. Read all `Sources/*.md` frontmatter once to compute the work sets for both phases.

## Phase A: stamp the cooldown clock

For each source with `done` checked and `done_date` empty, stamp `done_date` with today's date.
This is the only non-destructive phase; it starts the 7-day cooldown that Phase B counts from.

## Phase B: act after the cooldown

Phase B runs only when Basic Memory is healthy (see preamble). It acts on each source whose cooldown has elapsed (`done == true && done_date <= today - 7 days`), branching on `read`:

- `read` unchecked → discard the notebook without distilling (see kboat-notes "discard a source's notebook"); record it in the report as an unread discard. Idempotent: if `notebooklm_id` is already empty, skip.
- `read` checked and `distilled_date` empty → the source is **ripe**: distill it with the ordered steps below.

The ripe predicate is `done == true && read == true && done_date <= today - 7 days && distilled_date` empty.
Process each ripe source in this exact order. The order is what makes a crash safe: nothing the notebook holds is destroyed before it is durably recorded, and the `distilled_date` stamp is the commit point.

1. **Resolve the notebook.** Read `notebooklm_id` from the source note. Run `.venv/bin/notebooklm --quiet list --json`; if the id is absent (the notebook was deleted out of band), record it as an anomaly in the report and skip this source without stamping or discarding. Do not stamp `distilled_date` — nothing was distilled, and stamping it would falsely read as distilled. The source stays ripe and is re-surfaced each run until a human resolves it (e.g. clears the source note); this is the same contract as an extraction error in step 3.
2. **Resolve the source id.** Run `.venv/bin/notebooklm --quiet source list --notebook <notebooklm_id> --json`. With 1:1 there is exactly one source; take its id (sanity-check its `url`/`title` against the note). `fulltext` is keyed by source id.
3. **Extract** (read-only, safe to repeat). On any extraction error, abort this source — do not stamp, do not discard — record the error and continue to the next source.
   - `.venv/bin/notebooklm --quiet source fulltext <source_id> --notebook <notebooklm_id> -f markdown` — the content.
   - `.venv/bin/notebooklm --quiet history --notebook <notebooklm_id> --json` — the reading-time dialogue. The dialogue happens through the Gemini UI, which grounds answers in the notebook source but **also draws on web and world knowledge**, citing the sources it used. Keep those citations: a cited claim is source-grounded, an uncited one is external, and the accretion policy treats them differently.
   - `.venv/bin/notebooklm --quiet summary --notebook <notebooklm_id>` — NotebookLM's own summary (text only; no `--json`).
4. **Review material** (optional, best-effort). `.venv/bin/notebooklm --quiet generate flashcards --wait --json --notebook <notebooklm_id>` and/or `generate quiz`. These are async, so `--wait` is required. On timeout or error, mark them "pending" in the report and continue — do not abort the source.
5. **Distill into Basic Memory** following the accretion policy below.
6. **Write the review report** section for this source into `Reviews/YYYY-MM-DD.md` in the vault. Written before the discard, so the extracted material survives even if the discard fails.
7. **Stamp `distilled_date`** with today's date on the source note. This is the commit point; after it the source leaves the ripe set.
8. **Discard the notebook** (see kboat-notes). Always last. If it fails, the source is already distilled and the report is written — record "notebook discard failed" as a cleanup item for a later pass to reconcile.

A crash anywhere in 1–6 leaves the source ripe and replayable. A crash between 7 and 8 leaves a notebook to clean up later, never lost data.

## Accretion policy (unattended)

The `memory-ingest` skill assumes a human approves each new entity before it is created. This pass is unattended, so the approval gate becomes an **after-the-fact review gate**: every create, append, and skip decision is logged in the report for the human to reverse later.
Every Basic Memory call passes `project="k-boat-knowledge"` (see the top of this skill).

- **Append first.** Before creating a concept note, `search_notes` with at least three query variations: the exact title, a paraphrase, and the key English term if the concept has one. The project's embedding model is English (`bge-small`), so semantic recall on Japanese titles is weak — lean on these lexical variations. If a hit clears the bar, add to the existing note instead of creating a duplicate, with `edit_note(operation="insert_after_section", section="## Observations")` (or `"## Relations"`); plain `append` lands past the sections, so use `insert_after_section`. See `memory-curate`.
- **Create only specific concepts.** Auto-create a standalone note only for a clearly named concept (an algorithm, system, protocol, paper). For vague or broad concepts, do not create a note; log it as an "uncreated candidate" for the human to promote.
- **Cap creates per run.** Set a hard ceiling on new concept notes per run. If hit, stop creating, finish appends, and escalate in the report.
- **Ground every claim.** Treat `source fulltext` (and the source-grounded NotebookLM `summary`) as the authority. Tag each distilled observation by grounding: `#grounded` when the source supports it, `#dialogue` when it is external knowledge the conversation brought in (an uncited Gemini answer). Never let a `#dialogue` claim read as if it came from the source.
- **Double-check dialogue-derived claims.** A `#dialogue` claim is not source-grounded, so before keeping it, try to refute it against the source and your own knowledge. If it survives, accret it tagged `#dialogue`; if it fails or you are uncertain, do not accret it — log it as an uncreated candidate in the report. This keeps the genuinely useful outside knowledge the dialogue surfaced (the reason to chat while reading) while dropping the unverifiable — the safeguard for the lower-grounding Gemini-Flash dialogue.
- **Always record provenance.** Each contributing source adds, once per concept note, a provenance observation `- [source] <title> — <url>` carrying the source's canonical URL — it records that this source fed the concept, regardless of grounding; the per-claim `#grounded`/`#dialogue` tag records whether the claim came from the source or from external dialogue knowledge. The source note lives in the vault, a separate root, so a wikilink could not reach it; the URL is root-independent and keeps each autonomous decision traceable. (Relations *between concepts* stay wikilinks; both ends are in this root.)
- **Never auto-merge.** Merging concept notes is destructive and hard to reverse unattended. Log merge candidates in the report for `memory-curate` to handle with a human.
- **Stay idempotent on replay.** The project's `write_note` does not overwrite by default, so never issue a second `write_note` for the same concept — use `edit_note` with `insert_after_section`. Before inserting, check the section text and skip a provenance observation whose URL is already present, so a replay after a mid-run crash does not double-write.

## Review report (`Reviews/YYYY-MM-DD.md`)

One file per run, in the vault. For each distilled source, a decision log:

- `created:` new concept notes.
- `appended-to:` existing concept notes that grew.
- `kept from dialogue:` external (`#dialogue`) claims that passed the double-check.
- `skipped (dup of):` observations dropped as duplicates.
- `uncreated candidates:` concepts left for the human to promote — including `#dialogue` claims that failed the double-check or were uncertain.
- `merge candidates:` pairs flagged for `memory-curate`.

Plus the source's `summary` verbatim and any flashcards/quiz (or "pending"), the unread discards (sources whose notebook was discarded without distilling), and an anomalies section (notebook missing, discard failed, extraction errors).

## Run summary

End the run with counts (`done_date` stamps applied in Phase A; in Phase B: sources distilled, notebooks discarded unread, sources left for the next run by errors), whether the run stopped because the `k-boat-knowledge` project was missing or skipped Phase B for a Basic Memory outage, and every error with the source it affected and the cause.
