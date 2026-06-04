---
name: kboat-ingest
description: Ingest the "K-Boat Queue" macOS Reminders list into the Obsidian vault. Use when draining the queue into source notes, each with its own 1:1 NotebookLM notebook, whether triggered manually or by the scheduled routine. Defers to the kboat-notes skill for the note schema and file writing.
---

# K-Boat queue ingestion

Drain the `K-Boat Queue` list in macOS Reminders into the vault.
Each reminder is a title plus a URL, and becomes one source note with its own 1:1 NotebookLM notebook.
Follow the kboat-notes skill for the note schema, naming, and file writing.

## Prerequisites

- Run `.venv/bin/notebooklm auth refresh` before the batch, since NotebookLM cookies expire.
- Read the queue with [`rem`](https://github.com/bro3886/rem-cli): `rem list --list "K-Boat Queue" --incomplete --output json`.

## Per-item procedure

1. Decide the path, then gather signal. GET the URL with a browser User-Agent and sniff the bytes (not HEAD; see kboat-notes "Procedure: ingest a PDF source" for why and the exact rule): `%PDF-` ⇒ **PDF**; HTML with a PDF-shaped URL (`.pdf` / `/pdf/`) ⇒ **blocked PDF** (a bot-protection challenge — skip and report); HTML otherwise ⇒ **web page**. The extension alone never promotes to the PDF path — the bytes do (an arXiv `/pdf/<id>` link serves a real PDF).
   - **Web page**: fetch the page with a cheap subagent to extract a richer title than the reminder gives. The fetches run in parallel. If the fetch fails (bot protection, HTTP 4xx/5xx, timeout), record the error and fall back to the reminder title. (The durable `summary`/`topics` come later from the source guide, per kboat-notes, not this fetch.)
   - **PDF**: follow kboat-notes "Procedure: ingest a PDF source" instead of steps 2–3 below. The title comes from the abstract page (arXiv) or the PDF itself, not an HTML fetch; downloading the file, de-duplicating, and creating the notebook are all part of that procedure. The same commit-point rule holds — the source-note write is the commit point — and step 4 below still deletes the reminder only after that note exists.
2. Create a `Sources/*.md` note (see kboat-notes), using the fetched title. De-duplicate per kboat-notes — the URL-hash slug is the key: if `Sources/<slug>.md` already exists for this `url` and already has a `notebooklm_id`, it already has its notebook — skip step 3 and just update the note in place. The source-note write is the commit point: every queue item must end with a note on disk.
3. Create the source's 1:1 notebook (see kboat-notes "create or update a source note"): `create` → `source add` → **verify the fetch** (see kboat-notes: `ready` and the real article, not empty or a wall) → capture `summary`/`topics` from the source guide and write them plus `notebooklm_id` and the derived `gemini_url`/`notebooklm_url` back onto the source note. If the fetch is not successful, keep the note and notebook but record the source as "added but not fetched" in the run summary — its notebook has no usable content, and catching it here lets the content be supplied before distillation re-checks it. Run the verification in a cheap subagent, like the page fetch in step 1.
4. Delete the reminder only after the source note is written, with `rem delete <reminder_id> --force` (the id is the `id` field from the queue JSON). Keep the reminder if the source-note write fails, so the item is retried. A failure in step 3 alone does not keep the reminder — the note exists and the notebook can be created on a later pass.

## Safety

- De-duplicate per kboat-notes (the URL-hash slug); never create a second notebook for a source that already has a `notebooklm_id`.
- Keep the reminder if writing the source note fails.

## Errors

Do not work around errors for now; detect them and include them in the run summary (no retries, no bot-protection bypass).
Collect, per item, at least:

- Page fetch failures (bot protection, HTTP 4xx/5xx, timeout). The source note is still created with the reminder title as a fallback.
- Blocked PDF: the URL signals a PDF (`.pdf` / `/pdf/`) but the browser-UA GET returned HTML — a bot-protection challenge served instead of the file (e.g. scispace, which blocks `curl` even with a browser UA). Skip the item, keep its reminder, and report it so the PDF can be supplied another way.
- Undecidable type: the browser-UA GET failed outright (timeout, connection error) so neither path is safe to take. Skip the item and keep its reminder.
- PDF download failures (HTTP 4xx/5xx, timeout, zero size, or saved bytes not starting with `%PDF-`, e.g. an HTML challenge/error page — bot-protected hosts can return one intermittently even to a browser UA). No note is written; keep the reminder.
- Title fell back to the reminder text (the abstract page and the PDF's own metadata/first page all failed). The note is still created — flag it so a human can fix the title.
- Source guide failed (rate limit, error), so `summary`/`topics` are left empty. The note is still created; report it so a later pass or a human can fill them (recall falls back to `title`).
- `.venv/bin/notebooklm create` / `source add` failures (rate limit, auth). The source note is kept without a `notebooklm_id`; report it so a later pass or a human can give it a notebook.
- Sources that did not fetch successfully. For a web page: not `ready`, or a wall fetched instead of the article. For a PDF: `source wait` returned failed/timeout, or the upload extracted to empty/garbled text (see kboat-notes). The note, notebook, and any PDF file are kept, but the real content is missing — report them so it can be supplied another way.
- Source-note write failures. The reminder is kept (see Safety).
- Slug collisions: an existing `Sources/<slug>.md` holds a different `url` than the item being ingested (see kboat-notes de-dup). Stop that item without overwriting, keep its reminder, and report it; this is deterministic, so it needs a human to resolve rather than a retry.
- A failed `.venv/bin/notebooklm auth refresh` at the start. If auth is unusable, stop and report rather than processing the queue.

## Run summary

End the run with a summary covering:

- Counts: items drained, notebooks created, sources left without a notebook, sources added but not fetched (a web page blocked or walled, or a PDF that uploaded but did not extract), and sources left with empty `summary`/`topics`. For PDFs also count: download failures, and titles that fell back to the reminder text.
- Errors: each collected error with the item it affected and the cause (e.g. bot-blocked fetch, undecidable type, PDF download failure, rate-limited `create`/`source add`, empty PDF extraction, source-guide failure, note write failure, slug collision).
