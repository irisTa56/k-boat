---
name: kboat-rescue
description: Rescue a blocked (DLQ) K-Boat source — pull a bot-protected PDF or a walled web page through the real browser and finish ingesting it. Use when the user wants to complete a source that ingest could not fetch, or says things like "rescue this blocked source", "the scispace PDF is in the DLQ", "the Medium article is walled", "fetch <slug>", "complete the blocked one". Interactive and Mac-only: it drives the user's Chrome via Claude in Chrome and may ask them to solve a CAPTCHA or sign in. Defers to kboat-notes for the note schema and the rescue transitions.
---

# K-Boat rescue (DLQ → ingested)

Some sources cannot be fetched unattended: a bot-protected PDF behind an AWS WAF / Cloudflare CAPTCHA, or a walled web page (a member-only or paywalled article NotebookLM fetched as a login page). Ingest parks these in the **DLQ** as source notes with `blocked: true` and an empty `notebooklm_id` (see kboat-notes "The DLQ (blocked sources)"). This skill completes one: it obtains the content through the user's real browser — where a human can solve any CAPTCHA or sign in — builds the 1:1 notebook, and clears `blocked`, keeping the same note and `url`.

Follow kboat-notes for the schema and "Procedure: rescue a blocked source"; this skill adds the browser mechanics. It is **interactive and Mac-only** — it needs the user present and their Chrome connected. Do not run it from the unattended routine.

## Scope

Both blocked kinds are handled; they differ only in how the content is obtained and where the reading copy lives.

- **PDF** (`source_type: pdf`): the motivating case (scispace and similar `.pdf` URLs). A blocked PDF has a `url` and no local file yet; rescue saves the real file to `PDFs/<slug>.pdf` (the durable reading copy) and uploads it.
- **Web page** (`source_type: web_page`): a member-only or otherwise walled article. Rescue captures the rendered article text from the logged-in browser and ingests it as a NotebookLM text source; there is no local file — the reading copy stays the live `url`.

## Procedure

1. **Pick the source.** With a slug or `url` argument, load `Sources/<slug>.md` (or the note whose `url` matches) and confirm `blocked: true`; its `source_type` selects the PDF or web-page branch below. With no argument, read every `Sources/*.md` frontmatter, list those with `blocked: true` (their slug, title, `url`, `source_type`), and ask the user which to rescue. Load the env with `eval "$(mise env)"` at the top of every shell block (see kboat-notes "Environment"): it sets `$OBSIDIAN_VAULT_PATH` from `.env` and puts the venv on `PATH`, so the `notebooklm` commands resolve bare. The Bash tool keeps no shell state, so re-run it in each block.
2. **Confirm the browser.** Use Claude in Chrome: check `list_connected_browsers` returns a local browser. If none, fall back to the manual path (step 4).
3. **Fetch through the real browser.** Navigate the user's Chrome to the note's `url`. If a CAPTCHA / "Human Verification" / sign-in page appears, ask the user to clear it in their browser, then continue once the real content loads.
   - **PDF**: save it to `$OBSIDIAN_VAULT_PATH/PDFs/<slug>.pdf`. The capture mechanism is environment-dependent — Chrome may render the PDF inline or download it to `~/Downloads`; obtain the bytes whichever way works (trigger the download and move the newest `~/Downloads/*.pdf`, or read the response), then **verify the saved file starts with `%PDF-`** and is non-trivial in size. If it is not a real PDF (still a challenge), report and stop — leave `blocked: true`.
   - **Web page**: capture the rendered article text (`get_page_text` / `read_page`) once the real article is on screen, and write it to a temp file. **Read it back to confirm it is the article, not the wall** (a login form or paywall stub is short and generic); if it is still the wall, report and stop — leave `blocked: true`. No file is saved under the vault — the reading copy stays the `url`.
4. **Manual fallback.** If Claude in Chrome is unavailable or cannot get past the wall, ask the user to supply the content themselves and give a path: a downloaded PDF (copy it to `$OBSIDIAN_VAULT_PATH/PDFs/<slug>.pdf`, verify `%PDF-`), or the article text saved to a `.txt`/`.md` file (use it as the temp file in step 5).
5. **Finish ingestion** per kboat-notes "Procedure: rescue a blocked source": `create` (read `.notebook.id`) → set chat persona → add the source titled with the note's `title`, reading the returned source id from the `--json` output → `source wait <source_id> --notebook <id>` → verify extraction (`fulltext <source_id> --notebook <id> -o <tmpfile>`) → capture `summary`/`topics` (kboat-notes "capture summary and topics"). The add differs by branch:
   - **PDF**: `notebooklm --quiet source add "$OBSIDIAN_VAULT_PATH/PDFs/<slug>.pdf" --type file --mime-type application/pdf --title "<title>" --notebook <id> --json`.
   - **Web page**: `notebooklm --quiet source add - --type text --title "<title>" --notebook <id> --json < <tmpfile>` (the `-` reads the captured text from stdin as a text source, so a long article needs no shell-quoting). Titling it with the note's `title` is what lets the title-keyed source-id resolution find it later — the upload has no `url`.
   Then set `blocked: false`, write `notebooklm_id`/`gemini_url`/`notebooklm_url` and the captured `summary`/`topics`, and set `reading_link` = `[[<slug>.pdf]]` for a PDF (leave it as the `url` for a web page). The title comes from the note; refine it if the note's title was a fallback.
6. **Report.** State that the source left the DLQ and is now a normal source (in the inbox). Two non-clean endings:
   - **Wall not cleared** (no real PDF saved at step 3/4, or the captured text was still the wall): leave `blocked: true` — it stays in the DLQ.
   - **Empty extraction** (PDF only): a real PDF that extracts to nothing (an image-only scan) keeps `blocked: false` (the fetch succeeded), but report that the notebook is unusable and a text-bearing copy is needed — this is the ingest garbled-extraction case, not a DLQ state, so rescue does not re-fetch it. A web-page capture has no such case, since the text is supplied directly.

## Notes

- Identity is the `url` throughout; the slug (url-hash) never changes, so the rescued source reuses its existing note — no duplicate, provenance intact. `source_type` is unchanged by the rescue.
- A rescued web page's notebook source is a titled text upload with no `url`, so the on-demand source-id resolution matches it by `title` (like a `url: null` PDF upload), even though the note keeps its original `url`. See kboat-notes "One notebook per source (1:1)".
- A rescued web page has no durable local copy (unlike a PDF's `PDFs/<slug>.pdf`), so once its notebook is later discarded only the still-walled `url` remains: re-reading or re-dialogue needs another rescue capture, not a plain re-fetch. See kboat-notes "Procedure: reactivate a discarded source's notebook".
- This skill mutates: it creates a NotebookLM notebook and edits the source note, and for a PDF writes `PDFs/<slug>.pdf` (a web-page rescue writes no vault file). It never deletes the note.
