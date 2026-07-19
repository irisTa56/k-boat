# Architecture

This document is the deep reference for how feed-filter is put together.
`CLAUDE.md` carries the day-to-day operational rules; `README.md` is the user-facing guide.
Read this when a change touches more than one module or the Python ↔ skills contract.

## The split: deterministic Python vs. LLM

Everything verifiable and cheap is plain Python — fetching, feed/scrape parsing, discovery, URL canonicalization, the seen-store, and the vault writer.
The LLM is reserved for the two genuinely fuzzy judgments: picking the article cluster during site registration, and per-page keep/drop at run time.

A single `feed-filter <subcommand>` CLI emitting JSON on stdout is the **only** contract between the Python core and the Claude Code skills.
Skills never reach into Python internals.
The whole tool is synchronous: a sequential CLI batch over a sync `httpx.Client`, no `asyncio`.
The one exception is the article-path gather (`new-entries`), which fetches independent hosts concurrently with a bounded thread pool over the sync client — threads, not `asyncio`, and still no `asyncio` boundary anywhere.
Same-host sites are serialized within one worker, and the seen-store filter stays on the main thread, so the concurrency collapses only the network wall-clock (see the gather-concurrency invariant below).

## Python core (`src/feed_filter/`)

Pure primitives:

- `config.py` — constants and env-overridable paths (`FEED_FILTER_DB`, `FEED_FILTER_SITES`, `FEED_FILTER_SELECTION`), plus `vault_path()`, the `Feeds/`-note vault root read from `OBSIDIAN_VAULT_PATH`.
- `canonical.py` — URL canonicalization. **Always dedupe on `canonical_url`, never the raw URL.**
- `seen.py` — the sqlite seen-store; the sole dedupe authority.
- `site_health.py` — the durable per-site consecutive-failure counter (`site_health` table, v4 migration in `seen.py`); source-kind-agnostic, keyed by `site_id`. Operational telemetry, **not** dedupe/never-lost state (see the site-health invariant below).
- `body_cache.py` — a transient per-run cache of full feed bodies (`entry_body` table, v5 migration in `seen.py`), rewritten wholesale each `new-entries`. It keeps the full article off the run orchestrator's stdout/context: `new-entries` emits only a preview and the judge pulls the body via `entry-body`. Operational cache, **not** dedupe/never-lost state — a miss just falls the judge back to a `WebFetch`.
- `sites.py` — `sites.toml` read/write (via `tomlkit`, preserving formatting).

Deterministic httpx ingestion:

- `fetch.py` — sync `httpx` fetch; retries throttling statuses (`429`/`503`) up to a bounded count, honoring `Retry-After`, so the forum gather loop survives Discourse rate limits instead of shedding topics to the next run.
- `feeds.py` — feed parsing (`feedparser`). The entry `summary` is the richest available body flattened to plain text (`html_to_text`) and capped at `MAX_BODY_CHARS` (50k): a full-text feed (WordPress/Medium/Substack, e.g. thenewstack.io) ships the whole article in `content:encoded` (exposed by feedparser as `entry.content`) while `<description>` (`entry.summary`) is a truncated excerpt, so `_entry_body` prefers `content:encoded` and falls back to `<description>`. Handing the judge the full body lets it assess depth from the feed itself instead of forcing a per-article fetch that a gated site (Cloudflare) returns as a wall; the cap only guards against a pathological megabyte-scale body, never a normal article. That body reaches the judge through `body_cache` (not the run orchestrator's stdout, which carries only a preview), so the full article lands only in the cheap judge's context. The dedupe key is normally the resolved entry link, but a Medium item (`<guid>` of the form `medium.com/p/<hash>`) keys on that guid instead (`_dedupe_url`): Medium serves the same article under shifting link hosts (`medium.com/<pub>/` vs a publication custom domain like `netflixtechblog.com`), so the link forks the key and re-reminds when the host flips, while the `/p/<hash>` guid is invariant and 302-redirects to the live article. (The other historical fork, a `?source=` attribution param, is handled separately by `canonical.py`'s tracking-param stripping; the host shift is the residual cause the guid addresses.)
- `scrape.py` — index-page scraping (`selectolax`).

Discovery:

- `discover.py` — 4-layer zero-input feed/scrape discovery: self-feed → `rel=alternate` → typical-path probe → index-page clustering.

Browser path (opt-in):

- `browser.py` — synchronous Playwright gather: lazy single-instance Chromium, a cold non-persisted context, and the `require_playwright_*` install gates.
  The User-Agent is rebuilt without the `HeadlessChrome` marker to skip Cloudflare's first-line bot check.
  Replaying clearance cookies re-triggers the challenge on a headless session, so the context is deliberately not persisted.

Side effects and orchestration:

- `vault.py` — the vault output sink (`write_feed_note`); writes a kept entry as a hash-named `Feeds/` note via `kboat.write.upsert` (schema `FEED`), and raises `VaultError` on a slug collision so a failed write is never recorded seen.
- `pipeline.py` — per-site `gather_new` plus `fetch_entries`, branching to the httpx or browser transport on a site's `requires_browser` flag (seen-filter + per-site cap + the `zero_links` self-heal signal).
  `gather_new` is the sequential composition of `fetch_site` (network-only, DB-free, thread-safe) and `filter_gathered` (seen-filter + cap, main-thread only); `cmd_new_entries` drives the two halves separately to fetch hosts concurrently.
- `cli.py` + `__main__.py` — argparse subcommand dispatch tying it all together.
  Each browser-using command (`new-entries`, `add-site`, `heal-site`) gates on the Playwright install and tears the browser down in a `finally`.

## Discourse forum adapter (`src/feed_filter/`)

A second gather/judge/remind path for Discourse forums.
The forum kind extends the existing abstractions — `SiteConfig.kind == "forum"`, the seen-store migration framework, `parse_feed`/`Entry`, the shared `write_feed_note` vault sink — and adds only the genuinely new surface.

New modules:

- `discourse.py` — URL derivation (`latest_feed_url`, `top_feed_url`, `topic_json_url`), topic-id extraction (`topic_id_from_url`), and JSON parsing (`parse_topic` → `ForumTopic` + `list[ForumPost]`, reading `post_stream.posts` and taking each post's like count from `actions_summary id==2`). All HTTP and RSS goes through the reused `fetch`/`parse_feed` primitives.
- `forum_store.py` — the post-grain dedupe authority in its own two tables: `admit_topic` / `set_op_verdict` / `is_post_seen` / `record_post` / `due_topics` / `finalize_poll` / `last_like_count`.
- `forum_pipeline.py` — `admit_from_feeds` (RSS ingestion + Rule-A candidate emission) and `gather_forum` (due-topic Rule-B candidate assembly + `polled_topics` finalize worklist).

State store (v2 migration in `seen.py`, colocated for shared `user_version`):

- `forum_watch` — `(site_id, topic_id)` PK; tracks `first_seen_at`, `op_interest_kept`, `completed_polls`, `last_like_count`, `retired`, `poll_eligible` (v3).
  Admission is idempotent (`INSERT-OR-IGNORE`); retirement is offset-only.
- `forum_post_seen` — `(site_id, post_id)` PK; tracks `topic_id`, `kept`, `seen_at`.

Poll set is bounded to the top-N: only a topic surfaced by a top feed (`top.rss` daily ∪ weekly) is admitted `poll_eligible=1` and JSON-polled for Rule B.
A `latest.rss`-only topic is admitted for Rule-A judged-once tracking but never polled, so the per-run JSON sweep is the top-N, not every latest topic — the prior all-latest sweep tripped the forum's anonymous rate limit (429).
A topic first seen in `latest.rss` is upgraded to poll-eligible (one-directional) if it later appears in a top feed.

Watch/poll schedule: each poll-eligible topic is polled only at `poll_offsets_days` (default `[0, 1, 7]`) from first-seen.
A missed/offline run is caught up at the next run; several elapsed offsets collapse into one poll.
A topic retires after its last offset (`completed_polls >= len(offsets)`).
A topic whose JSON returns a permanent 404/410 (deleted/gone) advances its poll anyway, so a dead topic drains toward retirement instead of re-fetching every run; a transient error (throttle/5xx/transport) does not advance and re-polls next run.
A momentary 404 advances only one offset and self-corrects on the next successful poll, so the default `[0, 1, 7]` schedule tolerates a transient blip; a single-offset schedule (`[0]`) has no such tolerance — one 404 retires the topic, and retirement is not reversible.

The two rules:

- **Rule A** — the topic OP is judged once for cross-domain interest, with the forum's own `forum_subject` excluded as a match reason.
  Emitted by `admit_from_feeds` from the RSS `title`/`summary`; no JSON fetch needed.
  Two-stage: judge from the OP snippet first; `WebFetch` the topic page only when the snippet is too thin.
- **Rule B** — any post whose like count ≥ the effective threshold is judged for "worth-reading information"; native subject is not excluded.
  Effective threshold = `interest_like_threshold` when Rule A kept the topic, else `like_threshold`.
  Emitted by `gather_forum` from the topic JSON (`/t/<id>.json`).

Forum keeps become `Feeds/` notes like article keeps, hash-named by the topic URL and carrying `feed_kind: forum`; each note points at the topic top.
A re-reminded topic (a later qualifying post) upserts the *same* note idempotently — no duplicate — which subsumes the old "at most one open reminder per topic" suppression; the re-write resurfaces the topic by resetting `dismissed` to `false`, while the reader's `shelved` flag is preserved.
The forum path deliberately re-writes the note as new posts qualify, which is why it dedupes in its own tables (`forum_store.py`) rather than `seen.py` (see the dedupe-authority carve-out under Behavioral invariants).

## CLI subcommands (the JSON contract)

| Command | Purpose |
| --- | --- |
| `discover` | find feed/scrape candidates for a URL |
| `add-site` | register a site (snapshots seen, then writes config) |
| `list-sites` | list registered sites (with enabled/disabled status) |
| `new-entries` | gather new, unseen entries across non-forum sites (each entry's `summary` is a preview; the full body is cached for `entry-body`) |
| `entry-body` | print one gathered entry's full cached body (`{url, body}`; `body` is `null` on a cache miss) for the judge |
| `remind` | write a kept entry as a `Feeds/` note and record it seen (`--wall` flags a login/paywall page, `--summary` optional) |
| `mark-seen` | record a dropped entry seen (`kept=0`) |
| `heal-site` | rewrite a scrape pattern and re-snapshot |
| `disable-site` / `enable-site` | pause / resume a site without losing it |
| `add-forum` | register a Discourse forum (writes config only, no snapshot) |
| `forum-new` | gather Rule-A and Rule-B candidates across forum sites |
| `forum-remind` | write a kept forum topic as a `Feeds/` note and record the disposition (kept=1): `--post-id` → post-grain seen, `--is-op` → topic-grain verdict |
| `forum-mark-seen` | record a dropped disposition (kept=0); no note written; same `--post-id`/`--is-op` axes |
| `forum-poll-done` | advance the poll counter for one topic (call last, after all posts dispositioned) |

`new-entries` filters to `kind != "forum"` and `forum-new` filters to `kind == "forum"` — neither path ever sees the other's sites.
`new-entries` puts only a preview of each entry's body on stdout and caches the full body (`body_cache`); the judge pulls it with `entry-body`, so a full article is loaded into the cheap judging subagent's context, never the run orchestrator's.

## Skills (the orchestration layer)

`.claude/skills/` holds the four skills that drive the CLI. `prompts/selection.md` is the keep/drop prompt they feed each judging subagent.

- `kboat-add-feed-site` — main-model registration: discover → pick cluster → `add-site`.
- `kboat-feed-run` — the periodic article run: `new-entries` → haiku keep/drop → `remind`/`mark-seen` → self-heal.
- `kboat-manage-feed-sites` — ad-hoc pause/resume via `disable-site`/`enable-site`, plus on/off status from `list-sites`.
- `kboat-forum-run` — the periodic forum run: `forum-new` → Rule-A (Sonnet) / Rule-B (haiku) judgment → `forum-remind`/`forum-mark-seen` → `forum-poll-done`. Rule A is on the stronger model because the cross-domain call (native subject excluded, ecosystem tooling is not cross-domain) proved too subtle for haiku in practice.

## Behavioral invariants

These are the rules a multi-module change must preserve, each named with where it is enforced.
The user-facing narrative of the observable behavior is README's "Failure and self-heal behavior" — keep the two in sync.

- **Never-lost over never-duplicated.** `cmd_remind` (`cli.py`) writes the note *then* records seen, recording only on success; `write_feed_note` raises `VaultError` on a slug collision and lets an `OSError` from the atomic write propagate (`vault.py`), so a failed write is never recorded as seen. A judging error still writes the note (title or URL fallback) before recording. The only duplicate window is a crash in the gap between write and record — and the hash-named upsert makes even that re-run write idempotent, so nothing duplicates.
- **Never-lost at post grain (forum).** `cmd_forum_remind` mirrors the above for forum posts: the note write *then* the DB write, only on success. `cmd_forum_poll_done` must be the **last** call for a topic in a run, after every candidate is dispositioned — advancing the watch before disposition can cause a loss. A crash before `forum-poll-done` costs at most one re-poll; `forum_post_seen` dedups the already-seen posts, and the hash-named upsert makes a re-written note idempotent, so no duplicate results.
- **Two independent dedupe axes (forum).** Rule A and Rule B dedupe separately, and the OP may be taken up under both. A Rule-A disposition writes only the **topic-grain** verdict `forum_watch.op_interest_kept` (via `--is-op`); a Rule-B disposition writes only the **post-grain** `forum_post_seen` (via `--post-id`). The two flags are orthogonal in `cmd_forum_remind`/`cmd_forum_mark_seen`. Because Rule A reads the OP from RSS and never holds its `post_id`, it never marks the OP seen at post grain — so an OP dropped for interest under Rule A is still re-judged by Rule B if it later gains likes. An OP that is both interesting and popular is recorded under **both** axes, but yields **one** note: both `forum-remind` calls hash-name by the same topic URL, so the second upserts the same `Feeds/` note the first wrote — one note per topic, each disposition still recorded.
- **Seen-store is the dedupe authority at gather time (for non-forum sources).** Dedupe happens in `seen.py`/`pipeline.py` on `canonical_url`; the sink now also dedupes (a hash-named `kboat.write.upsert` is idempotent), but the seen-store remains the authority that stops an already-processed article from being re-gathered and re-judged. A gather-time fetch failure records nothing, so the next run retries — there is no *cross-run* backoff (within a single fetch, `fetch.py` does retry transient `429`/`503` throttling per `Retry-After`).
  **Forum carve-out:** the forum adapter is a second, post-grain dedupe authority in `forum_store.py` / `forum_post_seen`.
  Post-grain re-reminding is deliberate (a later post on a watched topic crosses the like bar); this suspends the "seen is final" invariant for forum sources only, leaving `seen.py` and every non-forum path untouched.
- **Operational notices never become notes.** The `Feeds/` folder holds only user-facing page notes (`vault.py`); self-heal and per-site errors are reported in the run's push summary, not as feed notes.
- **Scrape self-heal.** The `zero_links` signal (`pipeline.py`) means the stored `article_url_pattern` no longer matches the live index. `heal-site` (`cli.py`) re-scrapes under the new pattern and snapshots the matches as seen *before* rewriting `sites.toml` (snapshot-first / config-last, so a fetch failure never leaves a pattern with no snapshot under it).
- **Run bounds.** Per-site cap 20 and global cap 80 on entries/candidates judged (`DEFAULT_PER_SITE_CAP` / `DEFAULT_GLOBAL_CAP` in `config.py`).
- **Bounded per-host gather concurrency (article path).** `cmd_new_entries` fetches sites in two phases: the network-only `fetch_site` runs concurrently across hosts in a thread pool bounded at `DEFAULT_GATHER_CONCURRENCY` (16), then the DB-touching `filter_gathered` runs serially on the main thread in registry order. Two constraints are load-bearing: sites sharing a host (`_gather_host_key`) are grouped into one worker and fetched **in turn**, so no host is ever hit by two concurrent requests (crawler politeness — `sites.toml` has, e.g., six `aws.amazon.com` feeds); and `requires_browser` sites are fetched on the main thread because Playwright's sync API is not thread-safe. The concurrency collapses only the network wall-clock — it does not reorder results (round-robin is unchanged) or read the seen-store off the main thread. A fetch failure is still absorbed per site; an *unexpected* worker exception surfaces when its future drains, propagating out of the run exactly as the former sequential gather would. This exists because the gather was a slow sequential sum over ~80 sites (each up to `fetch.DEFAULT_TIMEOUT`), which pushed a run past the foreground timeout and forced backgrounding.
- **Site-health escalation.** Each run is stateless, so it cannot tell a chronic outage from a one-run blip on its own. `site_health.py` persists a per-site consecutive-failure counter, shared by both gather paths (source-kind-agnostic, keyed by `site_id`). `cmd_forum_new` increments it only when a forum site was **wholly unreachable** this run — every discovery feed raised `FetchError`, the typed `AdmitResult.all_feeds_failed` signal, never a heuristic parse of the error string — and resets it on any reachable run; a dead-topic retirement or a partial feed failure does **not** increment. `cmd_new_entries` mirrors this for article sites, which fetch a single feed/index: it increments when `gathered.error is not None` and resets otherwise, and a `zero_links` scrape (a broken pattern healed by `heal-site`, not an outage) does **not** increment. At `DEFAULT_PERSISTENT_FAILURE_RUNS` (3) the emitted `sites[]` entry flags `persistent`, and both run skills escalate: they fire the push and recommend checking for a moved URL first, then `disable-site` if truly gone. The CLI never auto-disables — a persistent failure is as often a recoverable migration (the elixirforum subdomain move) as a dead site, so termination stays a human decision.
  This write is **operational telemetry, outside the never-lost dedupe authority**: it touches no `seen`/`forum_post_seen`/`forum_watch`/`completed_polls` state, so a crash after it costs at most one extra increment on a failure that would recur anyway — never a lost post.
