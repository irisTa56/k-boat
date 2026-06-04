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

1. Fetch the page with a cheap subagent to extract its title, summary, and topics. This gives more signal than the reminder title alone, and the fetches run in parallel. If the fetch fails (bot protection, HTTP 4xx/5xx, timeout), record the error and fall back to the reminder title.
2. Create a `Sources/*.md` note (see kboat-notes), using the fetched title. De-duplicate by `url`: if a note with this `url` already exists and already has a `notebooklm_id`, it already has its notebook — skip step 3 and just update the note in place. The source-note write is the commit point: every queue item must end with a note on disk.
3. Create the source's 1:1 notebook (see kboat-notes "create or update a source note"): `create` → `source add` → **verify the fetch** (see kboat-notes: `ready` and the real article, not empty or a wall) → write `notebooklm_id` and the derived `gemini_url`/`notebooklm_url` back onto the source note. If the fetch is not successful, keep the note and notebook but record the source as "added but not fetched" in the run summary — its notebook has no usable content, and catching it here lets the content be supplied before distillation re-checks it. Run the verification in a cheap subagent, like the page fetch in step 1.
4. Delete the reminder only after the source note is written, with `rem delete <reminder_id> --force` (the id is the `id` field from the queue JSON). Keep the reminder if the source-note write fails, so the item is retried. A failure in step 3 alone does not keep the reminder — the note exists and the notebook can be created on a later pass.

## Safety

- De-duplicate by `url`; never create a second notebook for a source that already has a `notebooklm_id`.
- Keep the reminder if writing the source note fails.

## Errors

Do not work around errors for now; detect them and include them in the run summary (no retries, no bot-protection bypass).
Collect, per item, at least:

- Page fetch failures (bot protection, HTTP 4xx/5xx, timeout). The source note is still created with the reminder title as a fallback.
- `.venv/bin/notebooklm create` / `source add` failures (rate limit, auth). The source note is kept without a `notebooklm_id`; report it so a later pass or a human can give it a notebook.
- Sources that did not fetch successfully (see kboat-notes: not `ready`, or a wall fetched instead of the article). The note and notebook are kept, but the real content is missing — report them so it can be supplied another way (e.g. uploading the PDF or text into the notebook by hand).
- Source-note write failures. The reminder is kept (see Safety).
- A failed `.venv/bin/notebooklm auth refresh` at the start. If auth is unusable, stop and report rather than processing the queue.

## Run summary

End the run with a summary covering:

- Counts: items drained, notebooks created, sources left without a notebook, sources added but not fetched (blocked or returned a wall).
- Errors: each collected error with the item it affected and the cause (e.g. bot-blocked fetch, rate-limited `create`/`source add`, note write failure).
