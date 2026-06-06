---
name: feed-filter-run
description: Run one feed-filter pass — gather new entries from registered sites, judge each against selection.md with cheap haiku subagents, push keeps to the Filtered Feeds Reminders list, and self-heal broken scrape patterns. Use for the scheduled routine or a manual run.
---

# feed-filter periodic run

One pass of the filter: gather unseen entries across all registered sites, judge each against `selection.md`, remind the keeps, and record everything processed as seen.
This is the periodic, cost-sensitive half of feed-filter — the judging runs on **haiku** subagents (GUD-003), and the per-site/global caps (CON-005), not subagent cleverness, are the primary cost bound.

Run every `feed-filter` command from the repo root (`/Users/takayuki/Documents/_repos/feed-filter`).
The routine must run **locally** — `rem` writes the local Reminders.app, so a cloud run cannot push keeps (CON-001).
Each subcommand emits one JSON document on stdout and exits non-zero on an operational failure; parse the JSON and check the exit code.

## Prerequisites

- The **`Filtered Feeds`** Reminders list must already exist (user-created); the wrapper never creates lists, and a missing/renamed list makes `remind`/`heal-site` exit non-zero (CON-004). If reminds fail this way, stop and report — do not keep judging entries you cannot deliver.
- `rem` must be on `PATH` (Homebrew, DEP-007). A scheduled routine often starts with a PATH lacking `/opt/homebrew/bin`; ensure it is present (an absent `rem` surfaces as a non-zero `remind` exit, not a silent drop).
- Read the current criteria from `selection.md` once at the start of the run and pass them to every judging subagent. Honor a per-site `selection` override (from `feed-filter list-sites`, the `selection` field) when set — it replaces the Topics section for that site.

## Procedure

1. **Gather.** Run `feed-filter new-entries`.
   The output is `{entries: [{site_id, url, title, summary, kind}], sites: [{site_id, zero_links, error}]}`.
   - `entries` are the new, unseen items to judge, already round-robin-interleaved across sites and clamped to the global cap (REQ-010). Items dropped by the cap are simply absent and stay unseen — they reappear next run, so do not try to recover them here.
   - `summary` is `null` for `kind == "scrape"` (scrape entries carry no feed metadata).
   - Keep `sites` aside for steps 3–4.

2. **Judge each entry** with a **haiku** subagent, passing `selection.md` (plus any per-site override) and the entry. The subagent returns `{keep, wall, title, summary, reason}` (see `selection.md` "Output"). Judging the entries in parallel is fine.
   - **`kind == "feed"`** — two-stage to save cost (GUD-003): give the subagent the `title` and `summary` first and let it decide from those; only when they are too thin to judge does it `WebFetch` the page for the full text.
   - **`kind == "scrape"`** — there is no feed metadata, so the subagent goes straight to a full `WebFetch` of the `url`. The `title` it returns is authoritative — it is the **only** title source for the reminder (REQ-005).
   - Feeds **must not** be re-fetched with `WebFetch` for their item list (CON-002) — but `WebFetch` on an individual article page is exactly what the second stage is for.
   - **`wall == true`** — when a `WebFetch` returns a login wall / paywall / subscribe gate instead of the article, the subagent sets `wall = true` rather than guessing a keep/drop (selection.md "Walls and unreadable pages"). Wall detection is tied to the fetch: a `kind == "feed"` entry decided from its title+summary alone is never fetched, so it has no wall to flag — only scrape entries (always fetched) and feeds that fall through to the full-text fetch can surface a wall. This is distinct from a hard fetch error (the page would not load at all), handled in step 3.
   - **`requires_browser` site** (the `requires_browser` field from `feed-filter list-sites`) — the browser fetches only the *gather* feed/index, not the per-article body. The judging subagent's `WebFetch` is plain HTTP, so an article body behind the same gate the gather passed (e.g. Cloudflare) comes back as a wall/error: judge such an entry from its `title`+`summary` when those suffice, otherwise it falls to the wall path and is reminded for manual review (step 3) rather than content-filtered. A per-article browser fetch is a planned follow-up; until it lands such bodies are handed off, never lost.

3. **Act on each judged entry** (one `feed-filter` process per entry — the remind/record pair is atomic inside it).
   `remind` requires both `--title` and `--notes` (and `mark-seen` requires `--title`); always pass them, but an **empty string is allowed** — pass `--title ""` to invoke the URL fallback (REQ-005), and `--notes ""` when there is no summary. Omitting a required flag is an argparse error (exit 2), not a fallback, and would lose the entry.
   - **Wall** (`wall == true`) → take this branch **before** the keep/drop check: the page was a login/paywall, not the article, so defer to the user instead of dropping it (selection.md "Walls and unreadable pages"). Call `feed-filter remind --site-id <id> --url <url> --title "<title>" --notes "wall: needs manual review"`. **Prefer `--title ""`** (the URL fallback, reminders.py names the reminder after the canonical URL) unless the subagent extracted a genuine article title (e.g. from `og:title` left on the gate) — the visible page title on a wall is usually the gate's ("Sign in — …"), which is worse for manual review than the bare URL. This reminds and records seen (kept=1) like any keep, so the walled page is handed off once and not judged again.
   - **Keep** → `feed-filter remind --site-id <id> --url <url> --title <title> --notes <summary>`.
     This creates the reminder **and** records the entry seen (kept=1) in one process (REQ-009).
     Do **not** also call `mark-seen` — that would double-record.
     A non-zero exit means `rem` failed (e.g. the list vanished, CON-004) and the entry was **not** recorded seen; surface it and stop reminding (the failure will recur).
   - **Drop** → `feed-filter mark-seen --site-id <id> --url <url> --title <title>`.
     Records the entry seen (kept=0) with no reminder, so it is not judged again.
   - **Subagent or fetch error** on an entry → do not silently lose it (REQ-007). Call `feed-filter remind` anyway with the entry's `title` when it has one, otherwise `--title ""` so the CLI falls back to the URL (REQ-005), plus a `--notes` line saying judging failed: `feed-filter remind --site-id <id> --url <url> --title "<entry title or empty>" --notes "judging failed: <cause>"`. This deliberately favors never-lost over never-duplicated.

4. **Self-heal flagged scrape sites.** For each site in `sites` with `zero_links == true`, its stored `article_url_pattern` no longer matches the live index page (REQ-006) — not merely a quiet day. Repair it:
   - Re-run discovery on the site's `index_url` (`feed-filter discover <index_url>` — get it from `feed-filter list-sites`) and pick the article cluster's new `article_url_pattern`, exactly as the `feed-filter-add-site` skill does (a subagent to eyeball `sample_urls` is fine).
   - Run `feed-filter heal-site --site-id <id> --pattern <new_pattern>`.
     This re-scrapes the index under the new pattern, snapshots those URLs as seen (flood guard, kept=NULL), rewrites `sites.toml`, and files a `feed-filter:` alert reminder — all in one process, config written last (REQ-006).
   - A non-zero exit (no `{site_id, pattern, snapshotted, reminder_id}` JSON) has two cases, because the config write precedes the alert (config last, REQ-006).
     To tell them apart, check the site's `article_url_pattern` via `feed-filter list-sites`:
     - Still the **old** pattern → an **index-fetch failure** before anything was written; report it and let the next run retry the heal.
     - Already the **new** pattern → a **`rem`/alert failure** (the `Filtered Feeds` list vanished, CON-004) *after* the pattern was rewritten and the back-catalog snapshotted; the heal itself succeeded and only the advisory alert was lost, so do **not** retry — surface the `rem` failure instead.

5. **Surface errors.** For each site in `sites` with a non-null `error`, the gather fetch failed (REQ-008); the entry list was empty and nothing was recorded, so it retries naturally next run. Report it in the summary — a persistently broken feed is visible here, by design there is no backoff (RISK-002).

## Run summary

End the run with a concise summary:

- Counts: sites gathered, entries judged, kept (reminded), dropped, walled (reminded for manual review), error-fallback reminders (REQ-007).
- Self-heal: each site healed, with old → new pattern and how many URLs were re-snapshotted.
- Errors: each site with a gather `error`, and any `remind`/`heal-site` non-zero exit, with its cause.

## Cost controls (state these hold)

- Per-site cap `DEFAULT_PER_SITE_CAP=20` and global cap `DEFAULT_GLOBAL_CAP=80` bound how many entries any one run judges (CON-005); the caps, not the two-stage judgment, are the primary cost bound (GUD-003).
- Judging is one **haiku** subagent per page; scrape sites cost more because every entry is a full fetch with no metadata to short-circuit (RISK-003). Batching pages per subagent is the documented escalation if volume grows (ALT-006), not the default.
