---
name: kboat-rescue
description: Rescue a blocked (DLQ) K-Boat source — fetch a bot-protected or walled PDF through the real browser and finish ingesting it. Use when the user wants to complete a source that ingest could not fetch, or says things like "rescue this blocked source", "the scispace PDF is in the DLQ", "fetch <slug>", "complete the blocked one". Interactive and Mac-only: it drives the user's Chrome via Claude in Chrome and may ask them to solve a CAPTCHA. Defers to kboat-notes for the note schema and the rescue transitions.
---

# K-Boat rescue (DLQ → ingested)

Some sources cannot be fetched unattended: a bot-protected PDF behind an AWS WAF / Cloudflare CAPTCHA, or a walled web page. Ingest parks these in the **DLQ** as source notes with `blocked: true` and an empty `notebooklm_id` (see kboat-notes "The DLQ (blocked sources)"). This skill completes one: it obtains the content through the user's real browser — where a human can solve any CAPTCHA — saves it, builds the 1:1 notebook, and clears `blocked`, keeping the same note and `url`.

Follow kboat-notes for the schema and "Procedure: rescue a blocked source"; this skill adds the browser mechanics. It is **interactive and Mac-only** — it needs the user present and their Chrome connected. Do not run it from the unattended routine.

## Scope

- **PDF is the primary target** (the motivating case: scispace and similar `.pdf` URLs). A blocked PDF source has `source_type: pdf`, a `url`, and no local file yet.
- Walled **web pages** are a lighter, future extension (capture the rendered text as a `--type text` source); for now, handle PDFs and report web-page DLQ entries as needing manual handling.

## Procedure

1. **Pick the source.** With a slug or `url` argument, load `Sources/<slug>.md` (or the note whose `url` matches) and confirm `blocked: true`. With no argument, read every `Sources/*.md` frontmatter, list those with `blocked: true` (their slug, title, `url`), and ask the user which to rescue. Load the env with `eval "$(mise env)"` at the top of every shell block (see kboat-notes "Environment"): it sets `$OBSIDIAN_VAULT_PATH` from `.env` and puts the venv on `PATH`, so the `notebooklm` commands in step 5 resolve bare. The Bash tool keeps no shell state, so re-run it in each block.
2. **Confirm the browser.** Use Claude in Chrome: check `list_connected_browsers` returns a local browser. If none, fall back to the manual path (step 4).
3. **Fetch through the real browser.** Navigate the user's Chrome to the note's `url`.
   - If a CAPTCHA / "Human Verification" page appears, ask the user to solve it in their browser, then continue once the real content loads.
   - Save the PDF to `$OBSIDIAN_VAULT_PATH/PDFs/<slug>.pdf`. The capture mechanism is environment-dependent — Chrome may render the PDF inline or download it to `~/Downloads`; obtain the bytes whichever way works (trigger the download and move the newest `~/Downloads/*.pdf`, or read the response), then **verify the saved file starts with `%PDF-`** and is non-trivial in size. If it is not a real PDF (still a challenge), report and stop — leave `blocked: true`.
4. **Manual fallback.** If Claude in Chrome is unavailable or cannot get past the wall, ask the user to download the PDF themselves and give its path; copy it to `$OBSIDIAN_VAULT_PATH/PDFs/<slug>.pdf` and verify `%PDF-`.
5. **Finish ingestion** per kboat-notes "Procedure: rescue a blocked source": `create` → `source add "$OBSIDIAN_VAULT_PATH/PDFs/<slug>.pdf" --type file --mime-type application/pdf --title "<title>"` → `source wait` → verify extraction (`fulltext -o <tmpfile>`) → capture `summary`/`topics` (kboat-notes "capture summary and topics"). Once a real PDF is in hand the wall is cleared, so set `blocked: false`, write `notebooklm_id`/`gemini_url`/`notebooklm_url`, and set `reading_link` = `[[<slug>.pdf]]`. The title comes from the note; refine it from the PDF if the note's title was a fallback.
6. **Report.** State that the source left the DLQ and is now a normal PDF source (in the inbox). Two non-clean endings: if the wall could **not** be cleared (no real PDF was obtained at step 3/4), leave `blocked: true` — it stays in the DLQ. If a real PDF was obtained but it extracts to nothing (an image-only scan), keep `blocked: false` (the fetch succeeded) but report that the notebook is unusable and a text-bearing copy is needed — this is the ingest garbled-extraction case, not a DLQ state, so rescue does not re-fetch it.

## Notes

- Identity is the `url` throughout; the slug (url-hash) never changes, so the rescued source reuses its existing note — no duplicate, provenance intact.
- This skill mutates: it writes `PDFs/<slug>.pdf`, creates a NotebookLM notebook, and edits the source note. It never deletes the note.
