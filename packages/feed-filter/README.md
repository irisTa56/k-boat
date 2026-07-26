# feed-filter — feed, forum, and query triage

A simplified, Claude-Code-native reimplementation of the sibling project `loose-feeds` (a local checkout at `../loose-feeds`, not a public repo).

A local Claude Code scheduled routine periodically discovers new pages from registered sites, filters them against a prompt using cheap subagents, and writes the survivors as `type: feed` notes in the Obsidian vault's `Feeds/` folder.
A second routine does the same for registered Discourse forums, judging topics and popular posts on a sparse poll schedule and writing keeps as the same `Feeds/` notes.

## Design

Responsibilities split deterministically.
Plain Python owns everything verifiable and cheap — fetching, feed/scrape parsing, discovery, URL canonicalization, the seen-store, and the vault writer — exposed as a single `feed-filter` CLI.
The LLM owns only the genuinely fuzzy judgments: picking the article cluster during site registration, authoring the descriptions the query gather searches on (ad hoc — no skill drives it yet), and per-page keep/drop selection.

There are three ways a page reaches the filter. Two of them poll places you registered — article feeds and Discourse forums — so they return only what a known publisher published. The third, `query-new`, describes what you want in natural language and asks Exa for pages whose meaning matches, which is how a page on a site nobody registered becomes reachable.

The `feed-filter` CLI emits JSON on stdout and is the only contract between the Python core and the Claude Code skills; the skills never reach into Python internals.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the module-by-module map and the behavioral invariants.

## Prerequisites

- `OBSIDIAN_VAULT_PATH` set (via the workspace `.env`) to the Obsidian vault; kept entries are written as `Feeds/` notes under it. An unset vault path makes a write fail loudly rather than silently dropping kept entries.
- `mise` for the toolchain and tasks.
- `EXA_API_KEY` (workspace `.env`) for the query gather only. The feed and forum paths need no key; without one, `query-new` reports the missing key rather than failing.

## Setup

```sh
mise install          # install tools + generate the git pre-commit hook
cp prompts/selection.example.md prompts/selection.md   # then edit your keep/drop criteria
mise run pre-commit   # run the full QA gate (Python + markdown + secrets + link check)
mise run qa:py:feed-filter   # run this member's Python gate (ruff, ty, pytest, coverage floor)
```

## Usage

Registration and the periodic run are driven by Claude Code skills, which wrap the CLI.
The CLI can also be run directly from the repo root.

### Register a site

Use the `kboat-add-feed-site` skill, or run the CLI directly.
Discovery determines whether a URL is a feed or a scrape site; registration snapshots the current back-catalog as already-seen so only entries appearing *after* registration are ever written as notes.

```sh
# Inspect what discovery finds (feed candidates and/or scrape clusters):
feed-filter discover https://example.com/blog

# Feed site:
feed-filter add-site --id example --name "Example Blog" --feed-url https://example.com/feed.xml

# Scrape site (index page + article URL pattern):
feed-filter add-site --id example --name "Example Blog" \
  --index-url https://example.com/blog --article-url-pattern '^/blog/[^/]+/?$'

feed-filter list-sites
```

`sites.toml` (the registry), `prompts/selection.md` (the keep/drop criteria), and `feed-filter.db` (the seen-store) are **gitignored local state** — personal config that stays on your machine.
Only the `prompts/selection.example.md` template is committed (Setup copies it to `prompts/selection.md`); set `FEED_FILTER_SELECTION` to keep the active prompt elsewhere. Edit the registry by hand or via the skills.

### Pause or resume a site

To stop gathering a site without losing it — a chronically failing site, or one you simply want to mute for a while — disable it instead of deleting it:

```sh
feed-filter disable-site --site-id example   # the run skips it; config + seen-store kept
feed-filter enable-site  --site-id example   # resume; only post-resume entries are written
```

A disabled site is skipped entirely (no fetch, no error, no notification) and stays in `sites.toml` with an `enabled = false` line. Because its seen-store is preserved, re-enabling never floods the back-catalog. This is reversible and cheap, unlike deleting and re-registering (which re-runs discovery and loses history).

### Sites that need a browser (JS / anti-bot)

Most sites are fetched over plain HTTP.
A site that renders its feed or index with JavaScript, or gates it behind an anti-bot challenge such as Cloudflare, needs the opt-in browser path, which fetches through a headless Chromium driven by [Playwright](https://playwright.dev/python/).
Playwright is an optional dependency: a setup with no such site pulls in neither Playwright nor Chromium and pays nothing.

Install it only if you have such a site:

```sh
uv sync --extra browser && uv run playwright install chromium   # also pulls a large Chromium download
```

Register the site with `--requires-browser`; its cold-start snapshot then runs through the browser too:

```sh
feed-filter add-site --id example --name "Example (JS feed)" \
  --feed-url https://example.com/feed.xml --requires-browser
```

If the flag is set but the extra is not installed, the CLI fails fast with the exact install command — it never silently falls back to plain HTTP.

The anti-bot handling is deliberately narrow.
The browser context rebuilds Playwright's default User-Agent without the `HeadlessChrome` marker.
That marker is what Cloudflare's first-line bot check matches to serve a challenge headless Chromium cannot solve, so removing it makes those checks skip the challenge — there is no cookie seeding and no manual login step.
This covers the common first-line-bot-check class only; a site that still serves an interactive challenge after the User-Agent is normalized is unsupported, and surfaces as a recurring per-site error in run summaries rather than being worked around.

One further limit applies at judging time: the per-article body is fetched by the run skill's subagent over plain HTTP (`WebFetch`), not through the browser.
A body behind the same gate the browser gather passed is therefore unreadable to the judge, so such an entry is judged on its feed summary — or, when that is too thin, written as a feed note (flagged `wall`) for manual review rather than content-filtered.

### Run the filter

Use the `kboat-feed-run` skill. One pass gathers new entries across all non-forum sites, judges each against `prompts/selection.md` with a cheap **haiku** subagent, writes the keeps as `Feeds/` notes in the vault, and records everything processed as seen.
A run is bounded by a per-site cap (20) and a global cap (80) on entries judged.

### Search for pages by description

The query gather takes one or more natural-language descriptions instead of a site:

```sh
feed-filter query-new --query "personal write-ups about building your own map rendering or GPS trace tooling"
```

It emits the same entry shape as `new-entries` (plus the `query` that found each page), so the judging half of a run is identical — with one difference: a query entry has no cached body, so `entry-body` always misses for one and the judge fetches the page, exactly as it does for a scrape entry.
Pages already dispositioned (kept or dropped), or snapshotted at registration are dropped against the shared seen-store, so a query overlapping a registered feed costs no *judging*; the Exa request itself is already paid for by then, since the search precedes the dedupe.
Exa bills once per request, covering the first ten results; a result beyond ten is billed on top, so raising `--num-results` raises the per-query price.
A failed query is reported against itself and retried on the next run, leaving the others' results intact.

### Register a Discourse forum

Use the `kboat-add-feed-site` skill, or register directly:

```sh
# Register a Discourse forum (no back-catalog snapshot — admission is per-poll):
feed-filter add-forum --id erlang-forum --name "Erlang Forums" \
  --forum-url https://erlangforums.com \
  --forum-subject "Erlang"

# Optional tuning (all flags default to the values in config.py):
#   --like-threshold N           (default 6)  — Rule-B threshold when Rule A dropped the topic
#   --interest-like-threshold N  (default 3)  — Rule-B threshold when Rule A kept the topic
#   --daily-watch-count N        (default 3)  — how many daily-top topics to watch
#   --weekly-watch-count N       (default 5)  — how many weekly-top topics to watch
#   --poll-offsets-days N [N …]  (default "0 1 7") — days from first-seen at which to poll
```

Forum registration writes config only — no snapshot.
Unlike article sites (where a snapshot on registration prevents flooding the back-catalog on the first run), forum topics are admitted at poll time, so registration is safe without a snapshot.

### Run the forum filter

Use the `kboat-forum-run` skill. One pass gathers Rule-A and Rule-B candidates from all registered Discourse forum sites, judges each with a **haiku** subagent, writes the keeps as `Feeds/` notes in the vault, and advances each topic's poll counter.

- **Rule A** — the topic OP (first post) is judged once for cross-domain interest, with the forum's own subject (`--forum-subject`) excluded as a match reason. Judged from the RSS snippet; fetches the topic page only when the snippet is too thin to decide.
- **Rule B** — any post whose like count meets the effective threshold is judged for "worth-reading information"; the native subject is not excluded.

The two rules are independent axes. An OP dropped under Rule A (e.g. on-subject, so not cross-domain) is still re-judged by Rule B if it later gains likes, and an OP that is both cross-domain interesting and popular is recorded under both axes, but resolves to a single `Feeds/` note (see below).

Topics are polled on a sparse schedule (`--poll-offsets-days`, default 0, 1, and 7 days from first-seen) and retire after the last offset.
A re-reminded topic upserts the *same* `Feeds/` note (hash-named by the topic URL), so a topic never produces duplicate notes; the re-write resurfaces it by resetting the note's `read` and `dismissed` flags to `false`, so a topic the reader already finished with reappears when a new qualifying post arrives.

## Scheduling

The run **must execute locally** — the vault is the local iCloud folder, so a cloud routine cannot write the notes.
The Mac must be awake and the Claude runtime idle when the task fires.

Create a scheduled task (a `~/.claude/scheduled-tasks/<id>/SKILL.md`) whose prompt invokes the relevant run skill against this repo: `kboat-feed-run` for the article sites, `kboat-forum-run` for the Discourse forums.
The two are independent routines; register whichever you use, on whatever schedule you choose.
Guidance:

- Schedule it on an **off-:00 minute** (e.g. `17 * * * *` or a few times a day) to avoid the top-of-hour congestion when many routines fire at once.
- The task starts fresh each run with no memory of prior runs; the seen-store (`feed-filter.db`) is what carries state across runs, so the prompt only needs to point at this repo and the run skill.
- Ensure `OBSIDIAN_VAULT_PATH` is set in the task's environment (loaded from the workspace `.env`); an unset vault path surfaces as a non-zero exit, not a silent drop.
- A scheduled task runs only while the Claude app is open; if the app was closed when the task was due, it runs on next launch.

## Failure and self-heal behavior

The design favors **never-lost over never-duplicated**:

- An entry that errors during judging is written as a feed note anyway (with its title or a URL fallback) and then recorded seen — never silently dropped.
- A gather-time fetch failure records nothing seen, so the next run retries the site naturally; there is no backoff, so a permanently broken feed stays visible in run summaries by design.
- An *article* gather failure is contained to its own site, whether it is a fetch error or an unexpected one: one site cannot discard the entries the other ~80 already fetched. (The forum gather absorbs a fetch error per site but not yet an unexpected one.) An error the CLI could not classify is flagged as such, so the run summary reports it verbatim instead of narrating it as an unreachable site.
- The seen-store is the dedupe authority for article sources at gather time. The write-then-record pair runs in one process; a crash in the sub-millisecond gap between them re-runs the write next time, but the hash-named upsert makes that write idempotent — so even the crash window duplicates nothing.
- Forum sources keep a second dedupe authority scoped to individual posts, independent of the article seen-store, so a topic can re-write its feed note as later posts cross the like threshold (see [ARCHITECTURE.md](ARCHITECTURE.md) for the schema).
  The `forum-poll-done` step advances a topic's poll counter and must run **last**, after every candidate post is dispositioned — a crash before it costs at most one re-poll, never a lost post.

When a **scrape** site's index page yields zero pattern matches — the stored `article_url_pattern` no longer matches the live page, not merely a quiet day — the run self-heals: it re-runs discovery, re-picks the cluster, rewrites the pattern in `sites.toml`, snapshots the newly-matched URLs as seen (the same flood guard as registration), and reports the change in the run's summary. The heal writes no feed note; the feed notes are pages only, and operational notices go there too.
