---
name: feed-filter-add-site
description: Register a new site in feed-filter from its URL alone — run discovery, pick the article cluster, and add it to sites.toml with a cold-start snapshot. Use when the user wants to add/register a site to feed-filter, whether by URL or by name.
---

# Register a site in feed-filter

Add one site to the feed-filter registry so the periodic run starts watching it.
The user supplies only a URL; deterministic discovery decides whether it is a feed or a scrape site, and you pick the right article cluster when discovery offers more than one.
This is the infrequent, main-model half of feed-filter (GUD-004) — the periodic keep/drop half lives in the `feed-filter-run` skill.

Run every `feed-filter` command from the repo root (`/Users/takayuki/Documents/_repos/feed-filter`).
The `feed-filter` binary lives in the project venv, on `PATH` only after `eval "$(mise env)"`; a bare `feed-filter …` otherwise fails with `command not found`.
Each Bash call starts a fresh shell, so loading it once does not carry across calls — prefix every `feed-filter` command with `eval "$(mise env)" &&` (the first command below shows it; apply the same to every call).
The CLI emits one JSON document on stdout and exits non-zero on a transport/operational failure — parse the JSON, read the exit code, never scrape prose.

## Procedure

1. **Discover.** Run `eval "$(mise env)" && feed-filter discover <url>`.
   The output is `{candidates: [...], rejection: {reason, message} | null}`.
   - **Non-zero exit** → the initial URL could not be fetched (transport failure, CON-006). Report the error to the user and stop; do not register a site you could not reach.
   - **`rejection` is set** (exit 0, no usable candidate) → relay its actionable message instead of proceeding:
     - `needs_js` → the page renders its content with JavaScript, which the default httpx path cannot follow. Either ask for a server-rendered alternative URL (a feed link or a plain archive page), or — if the user wants this exact page as a scrape site — retry registration through the opt-in browser path with `--requires-browser` (see "Sites that need a browser" below). This rejection is the hint that a scrape index is JS-rendered.
     - `no_article_clusters` → no feed and no article-shaped link cluster was found. Ask the user to point at the site's article-listing/archive page (e.g. `/blog`, `/posts`, `/news`) rather than its landing page, and re-run discovery on that.
   - Otherwise you have one or more `candidates`.

2. **Pick the candidate.**
   - **Prefer a feed candidate** (`feed_type == "feed"`) when one exists — feeds carry titles and summaries, so the run is cheaper and more accurate (GUD-003). If several feeds surface, prefer the one whose `entry_count` and `sample_urls` look like the main article feed (not a comments or tag feed).
   - **Otherwise choose a scrape cluster.** Each scrape candidate carries `index_url`, `article_url_pattern`, and up to five `sample_urls`. Inspect the `sample_urls` and pick the cluster whose URLs are real articles, not navigation, tags, or pagination. Discovery already drops shallow nav clusters, but it emits every survivor — the judgment of which cluster is *the* article cluster is yours. Spinning up a subagent to eyeball the samples is at your discretion (GUD-004), not required.
   - If no candidate looks like real articles, do **not** guess — tell the user what was found and ask for a better listing URL.

3. **Choose an id and name.**
   - `--id` is a short, stable, unique slug (e.g. the domain stem, like `example-blog` for `example-blog.com`). It keys the seen-store and self-heal, so it must not collide with an existing site — run `feed-filter list-sites` if unsure.
   - `--name` is a human-readable label for reminders/summaries.

4. **Register.** Run the matching form:
   - **Feed:** `feed-filter add-site --id <id> --name <name> --feed-url <feed_url>`
   - **Scrape:** `feed-filter add-site --id <id> --name <name> --index-url <index_url> --article-url-pattern <article_url_pattern>`
   - Append `--requires-browser` for a JS / anti-bot site (see "Sites that need a browser" below).

   `add-site` snapshots the site's **current** entries into the seen-store **first** (durably), then writes `sites.toml` **last** (REQ-002).
   That snapshot is the cold-start flood guard: only entries that appear *after* registration are ever reminded.
   A non-zero exit means the back-catalog fetch failed before anything was written — report it and retry; the site was not registered.

5. **Confirm.** On success the output is `{site_id, kind, snapshotted}`.
   Tell the user the site was registered, its `kind` (feed or scrape), and how many existing entries were snapshotted as already-seen (so they understand nothing from the back-catalog will be reminded).

## Sites that need a browser (JS / anti-bot)

A site that renders its feed/index with JavaScript, or gates it behind an anti-bot challenge such as Cloudflare, is registered through the opt-in browser path by adding `--requires-browser` to `add-site`.
The flag needs the optional Playwright extra (`uv sync --extra browser && uv run playwright install chromium`); without it the command fails fast with that exact install command, so register such a site only once it is installed.

There are two ways you arrive here:

- **A known gated feed.** When the user already has the feed URL of a JS / anti-bot site, register it directly — `feed-filter add-site --id <id> --name <name> --feed-url <feed_url> --requires-browser` — and skip discovery. Discovery fetches over plain HTTP and would itself be blocked by the gate, so it never runs for such a site and never produces a `needs_js` hint; the operator supplies the feed URL.
- **A JS-rendered scrape index.** A `needs_js` rejection in step 1 is the hint to retry a scrape site through the browser: pick its `index_url` and `article_url_pattern` as usual, then add `--requires-browser`.

The cold-start snapshot of a `requires_browser` site runs through the browser too, so the flood guard holds exactly as on the httpx path.
The anti-bot handling covers Cloudflare's first-line bot check only (it normalizes the headless User-Agent); a site that still serves an interactive challenge is unsupported and surfaces as a recurring per-site error at run time, not at registration.

## Optional per-site selection override

If the user wants different keep/drop criteria for this one site, set its `selection` field.
It overrides the **Topics** section of `prompts/selection.md` for that site only (see `prompts/selection.md`).
Set it at registration with `feed-filter add-site … --selection "<criteria>"`, or add/change it later by editing the `selection = "..."` line under that site's `[[site]]` block in `sites.toml` (version-controlled config, so hand-editing is fine).

## Notes

- `sites.toml` is trusted, user-authored config — discovery applies no SSRF guard (SEC-001). Only register URLs the user intends.
- Discovery is deterministic and network-bound; if it is slow, it is fetching candidate feed URLs, not hanging.
