---
name: kboat-feed-run
description: Run one feed-filter pass — gather new entries from registered sites, judge each against prompts/selection.md with cheap haiku subagents, write keeps as Feeds/ notes in the Obsidian vault, and self-heal broken scrape patterns. Use for the scheduled routine or a manual run.
---

# feed-filter periodic run

One pass of the filter: gather unseen entries across all registered sites, judge each against `prompts/selection.md`, write the keeps into the vault, and record everything processed as seen.
This is the periodic, cost-sensitive half of feed-filter — the judging runs on **haiku** subagents, and the per-site/global caps, not subagent cleverness, are the primary cost bound.

Run every `feed-filter` command from the repo root.
The `feed-filter` binary lives in the workspace venv, on `PATH` only after `eval "$(mise env)"`; a bare `feed-filter …` otherwise fails with `command not found`.
Each Bash call starts a fresh shell, so loading it once does not carry across calls — prefix every `feed-filter` command with `eval "$(mise env)" &&` (the first command below shows it; apply the same to every call).
The routine must run **locally** — the vault is the local iCloud Obsidian folder (`OBSIDIAN_VAULT_PATH`), so a cloud run cannot write keeps into it.
Each subcommand emits one JSON document on stdout and exits non-zero on an operational failure; parse the JSON and check the exit code.

## Prerequisites

- `OBSIDIAN_VAULT_PATH` must be set (it comes from the workspace `.env`, loaded by `eval "$(mise env)"`). A keep becomes a `Feeds/<slug>.md` note there; the `Feeds/` folder is created on the first write. If the variable is unset, `remind` exits non-zero — stop and report rather than judging entries you cannot deliver.
- Read the current criteria from `prompts/selection.md` once at the start of the run and pass them to every judging subagent. This file is gitignored local config; if it is absent (a fresh checkout), stop and report that it must be created by copying `prompts/selection.example.md` to `prompts/selection.md` — do not judge with no criteria. Honor a per-site `selection` override (from `feed-filter list-sites`, the `selection` field) when set — it replaces the Topics section for that site.

## Procedure

1. **Gather.** Run `eval "$(mise env)" && feed-filter new-entries`.
   The output is `{entries: [{site_id, url, title, summary, kind}], sites: [{site_id, zero_links, error, unexpected_error, consecutive_failures, persistent}]}`.
   - `entries` are the new, unseen items to judge, already round-robin-interleaved across sites and clamped to the global cap. Items dropped by the cap are simply absent and stay unseen — they reappear next run, so do not try to recover them here.
   - `summary` is a **short preview** of the entry body (the first ~500 chars), not the full text, and is `null` for `kind == "scrape"` (scrape entries carry no feed metadata). The **full** feed body is deliberately kept off stdout — pull it on demand with `feed-filter entry-body --url <url>` (step 2), which keeps the whole article out of this orchestrating context and loads it only into the judging subagent's.
   - Each `sites` entry is `{site_id, zero_links, error, unexpected_error, consecutive_failures, persistent}`.
     `consecutive_failures` is a durable per-site count of consecutive runs whose gather errored, reset to 0 the moment a run succeeds; `persistent` is the CLI's verdict that this count crossed the escalation threshold.
     `unexpected_error` means the CLI absorbed an exception it could not classify — the failure did not arrive as a fetch error — and nothing more about whose fault it is (step 5).
     `persistent` is decided by the CLI, not re-judged here — a stateless run has no memory of prior runs, so the durable counter is what tells you a failure is chronic rather than a one-run blip. A `zero_links` scrape does not count as a failure — it is a broken pattern healed in step 4, not an outage.
   - Keep `sites` aside for steps 3–5.

2. **Judge each entry** with a **haiku** subagent, passing `prompts/selection.md` (plus any per-site override) and the entry. The subagent returns `{keep, wall, title, summary, reason}` (see `prompts/selection.md` "Output"). Judging the entries in parallel is fine.
   - **`kind == "feed"`** — staged to save cost: give the subagent the `title` and the preview `summary` first. If those already place the entry **outside the Topics**, drop it from the preview alone — no body fetch (prompts/selection.md "Walls and unreadable pages"). Otherwise the entry is plausibly in scope, so have the subagent run `feed-filter entry-body --url <url>` to load the **full** feed body into its own context and judge depth from that. Only when `entry-body` returns `body: null` (a cache miss) does it fall back to a `WebFetch` of the page for the full text.
   - **`kind == "scrape"`** — there is no feed metadata (and no cached body), so the subagent goes straight to a full `WebFetch` of the `url`. The `title` it returns is authoritative — it is the **only** title source for the note.
   - Feeds **must not** be re-fetched with `WebFetch` for their item list — but `entry-body` (the cached body) and a `WebFetch` of an individual article page are exactly what the later stages are for.
   - **`wall == true`** — when a `WebFetch` returns a login wall / paywall / subscribe gate instead of the article, the subagent sets `wall = true` rather than guessing a keep/drop (prompts/selection.md "Walls and unreadable pages"). Wall detection is tied to the `WebFetch`: an entry decided from its `title`+preview or from the cached `entry-body` body is never `WebFetch`ed, so it has no wall to flag — only scrape entries (always `WebFetch`ed) and a feed entry that both needs its full body and misses the `entry-body` cache can surface a wall. This is distinct from a hard fetch error (the page would not load at all), handled in step 3.
   - **`requires_browser` site** (the `requires_browser` field from `feed-filter list-sites`) — the browser fetches only the *gather* feed/index, not the per-article body. The feed body it parsed is cached, so `entry-body` still serves the full article for a feed that ships one (e.g. `content:encoded`) with no per-article fetch. Only when the feed body is too thin and the subagent falls back to a `WebFetch` does that plain-HTTP fetch hit the same gate the browser gather passed (e.g. Cloudflare) and come back as a wall/error — such an entry is kept for manual review (step 3) rather than content-filtered. A per-article browser fetch is a planned follow-up; until it lands such bodies are handed off, never lost. Decide relevance from the preview first: an entry the preview already places out of scope is dropped — never fetched, so never walled.

3. **Act on each judged entry** (one `feed-filter` process per entry — the write/record pair is atomic inside it).
   `remind` requires `--title` (and `mark-seen` requires `--title`); `--summary` is optional. Pass `--title ""` to invoke the URL fallback when there is no real title, and pass `--summary "<gist>"` when the judge returned one. Omitting `--title` is an argparse error (exit 2), not a fallback, and would lose the entry.
   - **Wall** (`wall == true`) → take this branch **before** the keep/drop check: the page was a login/paywall, not the article, so defer to the user instead of dropping it (prompts/selection.md "Walls and unreadable pages"). Call `feed-filter remind --site-id <id> --url <url> --title "<title>" --summary "<gist>" --wall`. The `--wall` flag sets the note's `wall` boolean, which the Feeds Base surfaces as a 🔒 prefix on the card. **Prefer `--title ""`** (the URL fallback) unless the subagent extracted a genuine article title (e.g. from `og:title` left on the gate) — the visible page title on a wall is usually the gate's ("Sign in — …"), which is worse for manual review than the bare URL. This writes the note and records seen (kept=1) like any keep, so the walled page is handed off once and not judged again.
   - **Keep** → `feed-filter remind --site-id <id> --url <url> --title <title> --summary <gist>`.
     This writes the `Feeds/` note **and** records the entry seen (kept=1) in one process.
     Do **not** also call `mark-seen` — that would double-record.
     A non-zero exit means the vault write failed and the entry was **not** recorded seen. Two cases, and the stdout tells them apart:
     - `{"status": "locked", "holder": …}` — a K-Boat run held the vault longer than the write waits (kboat-vault-conventions "Durability and the vault lock"). This does **not** recur, because the holder finishes: leave this entry for the next run and **carry on** with the remaining keeps. Report how many were deferred this way.
     - No `locked` record (a slug collision, an unset vault, a disk error) — surface it and stop reminding, since the failure will recur.
   - **Drop** → `feed-filter mark-seen --site-id <id> --url <url> --title <title>`.
     Records the entry seen (kept=0) with no note, so it is not judged again.
   - **Subagent or fetch error** on an entry → do not silently lose it. Call `feed-filter remind` anyway with the entry's `title` when it has one, otherwise `--title ""` so the CLI falls back to the URL, plus a `--summary` line saying judging failed: `feed-filter remind --site-id <id> --url <url> --title "<entry title or empty>" --summary "judging failed: <cause>"`. This deliberately favors never-lost over never-duplicated.

4. **Self-heal flagged scrape sites.** For each site in `sites` with `zero_links == true`, its stored `article_url_pattern` no longer matches the live index page — not merely a quiet day. Repair it:
   - Re-run discovery on the site's `index_url` (`feed-filter discover <index_url>` — get it from `feed-filter list-sites`) and pick the article cluster's new `article_url_pattern`, exactly as the `kboat-add-feed-site` skill does (a subagent to eyeball `sample_urls` is fine).
   - Run `feed-filter heal-site --site-id <id> --pattern <new_pattern>`.
     This re-scrapes the index under the new pattern, snapshots those URLs as seen (flood guard, kept=NULL), and rewrites `sites.toml` — one process, config written last. It writes **no** feed note (the heal is an operational notice, not a page); record the heal in the run summary instead. On success the output is `{site_id, pattern, snapshotted}`.
   - A non-zero exit means the index re-scrape failed *before* the config write (snapshot-first / config-last), so `sites.toml` still carries the old pattern and nothing was snapshotted; report it in the summary and let the next run retry the heal.

5. **Surface errors.** For each site in `sites` with a non-null `error`, that site's gather failed; the entry list was empty and nothing was recorded, so it retries naturally next run. Two independent fields decide what to write: `unexpected_error` says what **kind** of failure it was, and `persistent` says how hard to **escalate**.
   - **Kind — `unexpected_error == true`**: the CLI could not classify this failure — it did not arrive as a fetch error. That is all the flag asserts: it is usually a feed-filter bug, but a page feeding the parser something it rejects looks the same. So report the `error` **verbatim** with the site id and say it is unclassified, rather than narrating it as an unreachable site; do not diagnose it beyond what the message says.
   - **Escalation** — by design there is no backoff, so the durable `consecutive_failures`/`persistent` fields carry it (both kinds count toward them):
     - **Not `persistent`** (the common case): report the `error` in the summary and move on. A transient failure self-heals; the durable counter resets on the next successful gather, so do not escalate on a single bad run. An `unexpected_error` is worth reporting even on one run, because a failure the CLI could not classify is not self-evidently transient — report it, but still do not escalate it.
     - **`persistent == true`**: the site's gather has errored for `consecutive_failures` consecutive runs (the CLI has already decided this crossed the threshold — do not re-judge it as "transient"). Escalate: **flag it as actionable in the run summary** (see Run summary), recommending the two-step investigation below. The CLI never auto-disables — disabling stays your decision, because a persistent failure is as often a recoverable move as a dead site. When it is also an `unexpected_error`, say so and lead with the message: the investigation may end at "this is a bug to fix", but a chronically failing site still needs one.
       1. **Check first for a moved or renamed feed/index URL.** A "persistent" 5xx/4xx is frequently a site migration, not a dead site: e.g. the sibling forum path saw `elixirforum.com` move to the `forum.elixirforum.com` subdomain, its apex serving an unrelated 500 landing page that read as a chronic outage until the URL was updated. If the site moved, updating its `index_url`/`feed_url` (see `kboat-manage-feed-sites`) restores it with no loss — the seen-store keys on `canonical_url`, not the domain.
       2. **Only if the site is truly gone**, disable it with `feed-filter disable-site --site-id <id>` (see the `kboat-manage-feed-sites` skill).

## Run summary

Emit a run summary as the run's text output — the pass's durable record, and the only channel for operational notices (none become feed notes, which are pages only).
Lead with what is **actionable** — a gather `error` or an operational failure (a `remind` / `heal-site` non-zero exit, a missing-Playwright gate) — and name the offending sites so they can be fixed or paused.
A self-heal is worth surfacing too, but as an informational record (the run repaired the scrape pattern itself), not an action.
Routine keeps and walls need no callout — they land in the `Feeds/` notes you'll see in the Feeds Base, and a no-op run is unremarkable too.
A `persistent == true` site is **always** actionable — the escalation the durable counter exists to trigger, not a judgment call: surface it with the persistent site and step 5's recommendation (first check for a moved feed/index URL, else `feed-filter disable-site --site-id <id>`; see the `kboat-manage-feed-sites` skill), noting the `error` verbatim when it is an `unexpected_error`.
Whether to escalate this summary to a desktop notification is the unattended routine's concern — it owns the notification's fixed-string set; a manual run just reads the summary.

- Counts: sites gathered, entries judged, kept (written), dropped, walled (written for manual review), error-fallback writes.
- Self-heal: each site healed, with old → new pattern and how many URLs were re-snapshotted.
- Errors: each site with a gather `error` (noting whether it is an `unexpected_error`, its `consecutive_failures`, and whether it is `persistent`), any `remind` non-zero exit, and any `heal-site` re-scrape failure, with its cause.

## Cost controls (state these hold)

- Per-site cap `DEFAULT_PER_SITE_CAP=20` and global cap `DEFAULT_GLOBAL_CAP=80` bound how many entries any one run judges; the caps, not the two-stage judgment, are the primary cost bound.
- Judging is one **haiku** subagent per page; scrape sites cost more because every entry is a full fetch with no metadata to short-circuit. Batching pages per subagent is the documented escalation if volume grows, not the default.
