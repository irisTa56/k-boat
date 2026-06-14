# Architecture

This document is the deep reference for how feed-filter is put together.
`CLAUDE.md` carries the day-to-day operational rules; `README.md` is the user-facing guide.
Read this when a change touches more than one module or the Python ↔ skills contract.

## The split: deterministic Python vs. LLM

Everything verifiable and cheap is plain Python — fetching, feed/scrape parsing, discovery, URL canonicalization, the seen-store, and the `rem` wrapper.
The LLM is reserved for the two genuinely fuzzy judgments: picking the article cluster during site registration, and per-page keep/drop at run time.

A single `feed-filter <subcommand>` CLI emitting JSON on stdout is the **only** contract between the Python core and the Claude Code skills.
Skills never reach into Python internals.
The whole tool is synchronous: a sequential CLI batch over a sync `httpx.Client`, no `asyncio`.

## Python core (`src/feed_filter/`)

Pure primitives:

- `config.py` — constants and env-overridable paths (`FEED_FILTER_DB`, `FEED_FILTER_SITES`, `FEED_FILTER_SELECTION`).
- `canonical.py` — URL canonicalization. **Always dedupe on `canonical_url`, never the raw URL.**
- `seen.py` — the sqlite seen-store; the sole dedupe authority.
- `sites.py` — `sites.toml` read/write (via `tomlkit`, preserving formatting).

Deterministic httpx ingestion:

- `fetch.py` — sync `httpx` fetch; retries throttling statuses (`429`/`503`) up to a bounded count, honoring `Retry-After`, so the forum gather loop survives Discourse rate limits instead of shedding topics to the next run.
- `feeds.py` — feed parsing (`feedparser`).
- `scrape.py` — index-page scraping (`selectolax`).

Discovery:

- `discover.py` — 4-layer zero-input feed/scrape discovery: self-feed → `rel=alternate` → typical-path probe → index-page clustering.

Browser path (opt-in):

- `browser.py` — synchronous Playwright gather: lazy single-instance Chromium, a cold non-persisted context, and the `require_playwright_*` install gates.
  The User-Agent is rebuilt without the `HeadlessChrome` marker to skip Cloudflare's first-line bot check.
  Replaying clearance cookies re-triggers the challenge on a headless session, so the context is deliberately not persisted.

Side effects and orchestration:

- `reminders.py` — the injectable `rem` wrapper; raises on non-zero exit (CON-004).
- `pipeline.py` — per-site `gather_new` plus `fetch_entries`, branching to the httpx or browser transport on a site's `requires_browser` flag (seen-filter + per-site cap + the `zero_links` self-heal signal).
- `cli.py` + `__main__.py` — argparse subcommand dispatch tying it all together.
  Each browser-using command (`new-entries`, `add-site`, `heal-site`) gates on the Playwright install and tears the browser down in a `finally`.

## Discourse forum adapter (`src/feed_filter/`)

A second gather/judge/remind path for Discourse forums (Phase 2–5, `plan/feature-forum-adapter-1.md`).
The forum kind extends the existing abstractions — `SiteConfig.kind == "forum"`, the seen-store migration framework, `parse_feed`/`Entry`, the already-parameterized `add_reminder(list_name=…)` — and adds only the genuinely new surface.

New modules:

- `discourse.py` — URL derivation (`latest_feed_url`, `top_feed_url`, `topic_json_url`), topic-id extraction (`topic_id_from_url`), and JSON parsing (`parse_topic` → `ForumTopic` + `list[ForumPost]`, reading `post_stream.posts` and taking each post's like count from `actions_summary id==2`, FRM-004). All HTTP and RSS goes through the reused `fetch`/`parse_feed` primitives (FRM-GUD-001/002).
- `forum_store.py` — the post-grain dedupe authority in its own two tables: `admit_topic` / `set_op_verdict` / `is_post_seen` / `record_post` / `due_topics` / `finalize_poll` / `last_like_count`.
- `forum_pipeline.py` — `admit_from_feeds` (RSS ingestion + Rule-A candidate emission) and `gather_forum` (due-topic Rule-B candidate assembly + `polled_topics` finalize worklist).

State store (v2 migration in `seen.py`, colocated for shared `user_version`):

- `forum_watch` — `(site_id, topic_id)` PK; tracks `first_seen_at`, `op_interest_kept`, `completed_polls`, `last_like_count`, `retired`, `poll_eligible` (v3).
  Admission is idempotent (`INSERT-OR-IGNORE`); retirement is offset-only (FRM-007).
- `forum_post_seen` — `(site_id, post_id)` PK; tracks `topic_id`, `kept`, `seen_at`.

Poll set is bounded to the top-N (FRM-001): only a topic surfaced by a top feed (`top.rss` daily ∪ weekly) is admitted `poll_eligible=1` and JSON-polled for Rule B.
A `latest.rss`-only topic is admitted for Rule-A judged-once tracking but never polled, so the per-run JSON sweep is the top-N, not every latest topic — the prior all-latest sweep tripped the forum's anonymous rate limit (429).
A topic first seen in `latest.rss` is upgraded to poll-eligible (one-directional) if it later appears in a top feed.

Watch/poll schedule (FRM-007): each poll-eligible topic is polled only at `poll_offsets_days` (default `[0, 1, 7]`) from first-seen.
A missed/offline run is caught up at the next run; several elapsed offsets collapse into one poll.
A topic retires after its last offset (`completed_polls >= len(offsets)`).

The two rules (FRM-002/003):

- **Rule A** — the topic OP is judged once for cross-domain interest, with the forum's own `forum_subject` excluded as a match reason.
  Emitted by `admit_from_feeds` from the RSS `title`/`summary`; no JSON fetch needed (FRM-002).
  Two-stage: judge from the OP snippet first; `WebFetch` the topic page only when the snippet is too thin.
- **Rule B** — any post whose like count ≥ the effective threshold is judged for "worth-reading information"; native subject is not excluded (FRM-003).
  Effective threshold = `interest_like_threshold` when Rule A kept the topic, else `like_threshold`.
  Emitted by `gather_forum` from the topic JSON (`/t/<id>.json`).

Reminders land in the separate `Filtered Forums` list (FRM-005, `REMINDER_LIST_FORUM` in `config.py`); each reminder targets the topic top.
A reminder is suppressed when an *incomplete* reminder for the same topic URL already exists in the list (`open_reminder_urls`, FRM-006): two open reminders for one topic are redundant pointers to the same page, so at most one open reminder exists per topic — the disposition is still recorded, and once the reader completes that reminder a later qualifying post re-reminds.
The `Filtered Feeds` list is unchanged: the forum path deliberately re-reminds as new posts qualify, which is why it dedupes in its own tables rather than `seen.py` (see the dedupe-authority carve-out under Behavioral invariants, FRM-CON-001).

## CLI subcommands (the JSON contract)

| Command | Purpose |
| --- | --- |
| `discover` | find feed/scrape candidates for a URL |
| `add-site` | register a site (snapshots seen, then writes config) |
| `list-sites` | list registered sites (with enabled/disabled status) |
| `new-entries` | gather new, unseen entries across non-forum sites |
| `remind` | create a reminder and record it seen (kept) |
| `mark-seen` | record a dropped entry seen (`kept=0`) |
| `heal-site` | rewrite a scrape pattern and re-snapshot |
| `disable-site` / `enable-site` | pause / resume a site without losing it |
| `add-forum` | register a Discourse forum (writes config only, no snapshot) |
| `forum-new` | gather Rule-A and Rule-B candidates across forum sites |
| `forum-remind` | create a forum reminder and record the disposition (kept=1): `--post-id` → post-grain seen, `--is-op` → topic-grain verdict |
| `forum-mark-seen` | record a dropped disposition (kept=0); no reminder; same `--post-id`/`--is-op` axes |
| `forum-poll-done` | advance the poll counter for one topic (call last, after all posts dispositioned) |

`new-entries` filters to `kind != "forum"` and `forum-new` filters to `kind == "forum"` — neither path ever sees the other's sites (FRM-CON-001, finding #8).

## Skills (the orchestration layer)

`.claude/skills/` holds the four skills that drive the CLI. `prompts/selection.md` is the keep/drop prompt they feed each judging subagent.

- `feed-filter-add-site` — main-model registration: discover → pick cluster → `add-site`.
- `feed-filter-run` — the periodic article run: `new-entries` → haiku keep/drop → `remind`/`mark-seen` → self-heal.
- `feed-filter-manage-sites` — ad-hoc pause/resume via `disable-site`/`enable-site`, plus on/off status from `list-sites`.
- `feed-filter-forum-run` — the periodic forum run: `forum-new` → Rule-A (Sonnet) / Rule-B (haiku) judgment → `forum-remind`/`forum-mark-seen` → `forum-poll-done`. Rule A is on the stronger model because the cross-domain call (native subject excluded, ecosystem tooling is not cross-domain) proved too subtle for haiku in practice.

## Behavioral invariants

These are the rules a multi-module change must preserve, each named with where it is enforced.
The user-facing narrative of the observable behavior is README's "Failure and self-heal behavior" — keep the two in sync.

- **Never-lost over never-duplicated.** `cmd_remind` (`cli.py`) reminds *then* records seen, recording only on success; `add_reminder` raises `ReminderError` on a non-zero `rem` exit (`reminders.py`) so a failed remind is never recorded as seen. A judging error still reminds (title or URL fallback) before recording. The only duplicate window is a crash in the gap between remind and record, accepted to guarantee no loss.
- **Never-lost at post grain (forum).** `cmd_forum_remind` mirrors the above for forum posts: `rem add` *then* the DB write, only on success (FRM-CON-005). `cmd_forum_poll_done` must be the **last** call for a topic in a run, after every candidate is dispositioned — advancing the watch before disposition can cause a loss (FRM-CON-005 / FRM-PAT-001). A crash before `forum-poll-done` costs at most one re-poll; `forum_post_seen` dedups the already-seen posts so no duplicate remind results.
- **Two independent dedupe axes (forum, FRM-006).** Rule A and Rule B dedupe separately, and the OP may be taken up under both. A Rule-A disposition writes only the **topic-grain** verdict `forum_watch.op_interest_kept` (via `--is-op`); a Rule-B disposition writes only the **post-grain** `forum_post_seen` (via `--post-id`). The two flags are orthogonal in `cmd_forum_remind`/`cmd_forum_mark_seen`. Because Rule A reads the OP from RSS and never holds its `post_id` (FRM-002), it never marks the OP seen at post grain — so an OP dropped for interest under Rule A is still re-judged by Rule B if it later gains likes. An OP that is both interesting and popular is recorded under **both** axes, but reminded **once**: the second `forum-remind` for the same topic URL is suppressed when an open reminder already exists (`open_reminder_urls`), since both point to the same topic top — at most one open reminder per topic, the disposition still recorded.
- **Seen-store is the sole dedupe authority (for non-forum sources).** Dedupe happens in `seen.py`/`pipeline.py` on `canonical_url`; `rem` does not dedupe. A gather-time fetch failure records nothing, so the next run retries — there is no *cross-run* backoff (within a single fetch, `fetch.py` does retry transient `429`/`503` throttling per `Retry-After`).
  **Forum carve-out:** the forum adapter is a second, post-grain dedupe authority in `forum_store.py` / `forum_post_seen`.
  Post-grain re-reminding is deliberate (a later post on a watched topic crosses the like bar); this suspends the "seen is final" invariant for forum sources only, leaving `seen.py` and every non-forum path untouched (FRM-CON-001).
- **Operational notices never enter the list.** The `Filtered Feeds` list and the `Filtered Forums` list both hold only user-facing items (`reminders.py`); self-heal and per-site errors are reported in the run's push summary, not as list reminders.
- **Scrape self-heal.** The `zero_links` signal (`pipeline.py`) means the stored `article_url_pattern` no longer matches the live index. `heal-site` (`cli.py`) re-scrapes under the new pattern and snapshots the matches as seen *before* rewriting `sites.toml` (snapshot-first / config-last, so a fetch failure never leaves a pattern with no snapshot under it).
- **Run bounds.** Per-site cap 20 and global cap 80 on entries/candidates judged (`DEFAULT_PER_SITE_CAP` / `DEFAULT_GLOBAL_CAP` in `config.py`).
