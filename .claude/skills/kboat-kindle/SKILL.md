---
name: kboat-kindle
description: Ingest a Kindle book into the K-Boat vault from its reader URL. Use when the user wants to add a book they read (or are reading) on Kindle, or pastes a `https://read.amazon.co.jp/?asin=...` URL (or a bare ASIN) and says things like "add this book", "ingest this Kindle title", "save this to my Kindle shelf". Interactive and Mac-only: it reads the book's metadata from Amazon through the user's logged-in Chrome (Claude in Chrome). Defers to kboat-notes for the Kindle-note schema and the create transitions.
---

# K-Boat Kindle ingest

Adds one Kindle book to the vault as a `type: kindle` note (`Kindles/<ASIN>.md`). Given a Kindle reader URL (`https://read.amazon.co.jp/?asin=<ASIN>`) or a bare ASIN, it reads the book's bibliographic metadata from the Amazon product page through the user's logged-in Chrome and writes the note, with the reader URL recorded as `reading_link`.

Follow kboat-notes for the schema and "Procedure: create or update a Kindle note"; this skill adds the browser mechanics. It is **interactive and Mac-only** — it needs the user present and their Chrome connected. Do not run it from the unattended routine.

A Kindle note has no NotebookLM notebook and no fetched URL: it is a permanent catalogue entry whose **body** holds reading highlights that distillation later draws on. This skill creates the entry with an empty body; the highlights are added afterwards (by hand or with the `organize-reading-note` skill).

## Why the browser

Amazon JP serves a bot-defense stub to anonymous fetches (`curl`, `WebFetch`), and a Kindle ASIN (`B0…`) is not an ISBN, so the ISBN-keyed metadata APIs cannot resolve it. The reliable path is the user's real, logged-in Chrome — the same mechanism kboat-rescue uses for walled PDFs. The trade-off is that ingest is interactive and macOS-only rather than deterministic; that is acceptable because adding a finished book is itself a manual moment.

## Procedure

1. **Resolve the ASIN.** From a reader URL take the `asin` query parameter (`https://read.amazon.co.jp/?asin=<ASIN>`); a bare ASIN is used verbatim. This is the de-dup key. Read the vault from `$OBSIDIAN_VAULT_PATH`, loaded from `.env` by `eval "$(mise env)"` (see kboat-notes "Environment").
2. **De-dup.** If `Kindles/<ASIN>.md` already exists, this is the same book — report it as already recorded and stop (do not re-extract), unless the user asked to refresh its metadata, in which case update it in place. The filename, being the ASIN, never changes.
3. **Confirm the browser.** Use Claude in Chrome: check `list_connected_browsers` returns a local browser. If none, fall back to the manual path (step 5).
4. **Extract metadata through the real browser.** Navigate the user's Chrome to `https://www.amazon.co.jp/dp/<ASIN>` and read the page:
   - `title` — the product title, in the page's own language (keep a Japanese title Japanese).
   - `author` — every listed contributor (author/translator) from the **byline** under the title (the `by … (Author)` line), as a list. Prefer this byline over the "Follow the author" / "About the author" widget, which can name a different person (e.g. a corporate author in the byline but an individual in the widget).
   - `publisher` and `published` — from the product-details / 登録情報 section. Keep `published` at whatever precision is shown (`YYYY`, `YYYY-MM`, or `YYYY-MM-DD`); do not zero-pad to fake precision.
   - If a CAPTCHA / "Human Verification" / sign-in wall appears, ask the user to clear it in their browser, then continue once the product page loads. If the page is unavailable or extraction is ambiguous (wrong product, missing fields), show the user what you found and ask them to confirm or supply the missing fields.
5. **Manual fallback.** If Claude in Chrome is unavailable or the wall cannot be cleared, ask the user for the title, author(s), publisher, and publication date, and proceed with those.
6. **Write the note** per kboat-notes "Procedure: create or update a Kindle note": `Kindles/<ASIN>.md` with `type: kindle`, the extracted fields, `reading_link` = the reader URL (directly under `title`), `store_link` = the product-page link `https://www.amazon.co.jp/dp/<ASIN>`, `added_date` = today; `reading`, `finished`, and `distill` start `false`; `distilled_date` empty. Leave the body empty. There is no `isbn` field (a Kindle page shows the ASIN, not an ISBN).
7. **Report.** State that the book was added (or already present), echo the resolved fields, and remind the user that reading highlights go in the note body — added by hand or with `organize-reading-note` — that `reading` and `finished` track reading progress (checking `finished` drops the book off the Base reading-list view), and that checking `distill` opts it into the next distillation run.

## Notes

- Identity is the ASIN throughout; the filename (the ASIN) never changes, so re-running on the same book reuses the existing note — no duplicate.
- This skill mutates only the source-of-record note: it writes `Kindles/<ASIN>.md`. It creates no NotebookLM notebook and deletes nothing.
- Future extensions (not done here): pulling Kindle highlights from `read.amazon.co.jp/notebook` to fill the body automatically, and auto-routing read.amazon URLs dropped into the `Queue/` folder.
