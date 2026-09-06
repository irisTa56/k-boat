---
name: kboat-add-feed-site
description: Register a new site in feed-filter from its URL alone. For an article feed/scrape site, run discovery, pick the article cluster, and snapshot the back-catalog; for a Discourse forum, register it with add-forum (no discovery, no snapshot). Use when the user wants to add/register a site or a Discourse forum to feed-filter, whether by URL or by name.
---

# Register a site in feed-filter

Add one site to the feed-filter registry so the periodic run starts watching it.
The user supplies only a URL; deterministic discovery decides whether it is a feed or a scrape site, and you pick the right article cluster when discovery offers more than one.
This is the infrequent, main-model half of feed-filter — the periodic keep/drop half lives in the `kboat-feed-run` skill for article sites and in the `kboat-forum-run` skill for forums.

Run every `feed-filter` command from the repo root.
The `feed-filter` binary lives in the workspace venv, on `PATH` only after `eval "$(mise env)"`; a bare `feed-filter …` otherwise fails with `command not found`.
Each Bash call starts a fresh shell, so loading it once does not carry across calls — prefix every `feed-filter` command with `eval "$(mise env)" &&` (the first command below shows it; apply the same to every call).
The CLI emits one JSON document on stdout and exits non-zero on a transport/operational failure — parse the JSON, read the exit code, never scrape prose.

## Article site or Discourse forum?

feed-filter watches two kinds of source, registered by two different commands — decide which the user means before doing anything.

- An **article site** (a blog/news feed, or a scrapeable article index) → discovery + `add-site`, the **Procedure (article site)** below.
- A **Discourse forum** (post-grain watching with Rule A/B, keeps written as `feed_kind: forum` notes) → `add-forum`, with **no discovery and no snapshot** — skip to [Registering a Discourse forum](#registering-a-discourse-forum).

Take the forum branch when the user is registering a forum (they say "forum" or "Discourse", or the URL is a Discourse instance).
A forum is a distinct source kind: registering it through `add-site` would mis-file it as a feed/scrape site (an `article` note), and the forum rules would never run.
When unsure which a URL is, confirm before registering — a Discourse instance serves a feed at `<url>/latest.rss`.

## Procedure (article site)

1. **Discover.** Run `eval "$(mise env)" && feed-filter discover <url>`.
   The output is `{candidates: [...], rejection: {reason, message} | null}`.
   - **Non-zero exit** → the initial URL could not be fetched (a transport failure).
     - Report the error to the user and stop; do not register a site you could not reach.
   - **`rejection` is set** (exit 0, no usable candidate) → relay its actionable message instead of proceeding:
     - `needs_js` → two different failures share this reason, told apart by the message, and they take different next steps.
       - An HTML page whose links did not cluster into articles → likely a JavaScript-rendered index, which the default httpx path cannot follow.
         - Either ask for a server-rendered alternative URL (a feed link or a plain archive page), or — if the user wants this exact page as a scrape site — retry registration through the opt-in browser path with `--requires-browser` (see "Sites that need a browser" below).
       - An empty body, or one the server did not label as HTML → discovery stopped before the clustering step, so it has established nothing about the page's article links.
         - Ask the user for a URL that serves the site's articles as HTML — its article-listing page, or a feed — and re-run discovery on that.
         - Where they say the URL they gave already is that page, stop and report that the response it returns carries nothing discovery can read articles from, rather than asking again.
     - `no_article_clusters` → no feed and no article-shaped link cluster was found.
       - Ask the user to point at the site's article-listing/archive page (e.g. `/blog`, `/posts`, `/news`) rather than its landing page, and re-run discovery on that.
   - Otherwise you have one or more `candidates`.

2. **Pick the candidate.**
   - **Prefer a feed candidate** (`feed_type == "feed"`) when one exists — feeds carry titles and summaries, so the run is cheaper and more accurate.
     - If several feeds surface, prefer the one whose `entry_count` and `sample_urls` look like the main article feed (not a comments or tag feed).
   - **Otherwise choose a scrape cluster.** Each scrape candidate carries `index_url`, `article_url_pattern`, and up to five `sample_urls`.
     - Inspect the `sample_urls` and pick the cluster whose URLs are real articles, not navigation, tags, or pagination.
       - Discovery already drops shallow nav clusters, but it emits every survivor — the judgment of which cluster is *the* article cluster is yours.
       - Spinning up a subagent to eyeball the samples is at your discretion, not required.
   - If no candidate looks like real articles, do **not** guess — tell the user what was found and ask for a better listing URL.

3. **Choose an id and name.**
   - `--id` is a short, stable, unique slug (e.g. the domain stem, like `example-blog` for `example-blog.com`).
     - It keys the seen-store and self-heal, so it must not collide with an existing site — run `feed-filter list-sites` if unsure.
   - `--name` is a human-readable label for the site (used in the notes/summaries).

4. **Register.** Run the matching form:
   - **Feed:** `feed-filter add-site --id <id> --name <name> --feed-url <feed_url>`
   - **Scrape:** `feed-filter add-site --id <id> --name <name> --index-url <index_url> --article-url-pattern <article_url_pattern>`
   - Append `--requires-browser` for a JS / anti-bot site (see "Sites that need a browser" below).

   `add-site` snapshots the site's **current** entries into the seen-store **first** (durably), then writes `sites.toml` **last**.
   That snapshot is the cold-start flood guard: only entries that appear *after* registration are ever written as notes.
   A non-zero exit means the back-catalog fetch failed before anything was written — report it and retry; the site was not registered.

5. **Confirm.** On success the output is `{site_id, kind, snapshotted}`.
   Tell the user the site was registered, its `kind` (feed or scrape), and how many existing entries were snapshotted as already-seen (so they understand nothing from the back-catalog will be written as a note).

## Registering a Discourse forum

A Discourse forum is registered with `add-forum`, not `add-site`.
There is **no discovery** (there is no article cluster to pick) and **no cold-start snapshot**: forum topics are admitted at poll time, so a snapshot would silently discard every topic due for first-run Rule-A judgment — a loss, not a flood guard.

1. **Confirm it is a Discourse forum.** Unless the user has already made that clear, verify the instance serves `<forum_url>/latest.rss` (a Discourse RSS feed) before registering.
   `add-forum` validates only the config shape, so a non-Discourse URL registers cleanly but then fails every run with a per-site fetch error.
   A quick `WebFetch` of the forum's landing page (or `/latest.rss`) both confirms Discourse and gives you the subject for the next step.

2. **Choose an id and name** — same rules as the article path: `--id` a short, stable, unique slug (check `feed-filter list-sites` for collisions), `--name` a human-readable label.

3. **Pick the native subject (`--forum-subject`).** This is the forum's own domain — e.g. `Erlang` for `erlangforums.com`.
   It is excluded as a Rule-A match reason so the run keeps only topics interesting *outside* this forum's community, not the on-subject ones.
   Infer it from the forum's title/description, or ask the user.
   Omit it for a general-interest forum with no single subject (Rule A then judges on interest alone).

4. **Optional tuning** — each flag defaults to the value in `config.py`, so pass only what the user wants to change:
   - `--like-threshold N` (default 6) — Rule-B like bar when Rule A dropped the topic.
   - `--interest-like-threshold N` (default 3) — Rule-B like bar when Rule A kept the topic.
   - `--daily-watch-count N` (default 3) / `--weekly-watch-count N` (default 5) — how many daily / weekly top topics to watch.
   - `--poll-offsets-days N [N …]` (default `0 1 7`) — days from first-seen at which to poll; the topic retires after the last offset.

5. **Register.** `feed-filter add-forum --id <id> --name <name> --forum-url <forum_url> [--forum-subject <subject>] [tuning…]`.
   This writes `sites.toml` only — no snapshot.
   A non-zero exit means the arguments were rejected — a missing or malformed flag (argparse), or a config-shape error from `SiteConfig`; fix the args and retry.

6. **Confirm.** On success the output is `{site_id, kind, forum_url}` with `kind == "forum"`.
   Tell the user the forum was registered and that keeps will be written as `Feeds/` notes in the vault (`feed_kind: forum`).
   Nothing else is needed: the `Feeds/` folder is created on the first write, and the run only needs `OBSIDIAN_VAULT_PATH` set (from the workspace `.env`).

A forum's per-site `selection` override is not an `add-forum` flag.
Set it later by hand-editing the `selection = "..."` line under that forum's `[[site]]` block in `sites.toml` (the forum run honors it, replacing the Topics section for that forum only).
That file is gitignored personal state (see `packages/feed-filter/CLAUDE.md`), so no checkout restores a bad edit — report the old value with the change.

## Sites that need a browser (JS / anti-bot)

A site that renders its feed/index with JavaScript, or gates it behind an anti-bot challenge such as Cloudflare, is registered through the opt-in browser path by adding `--requires-browser` to `add-site`.
The flag needs the optional Playwright extra (`uv sync --extra browser && uv run playwright install chromium`); without it the command fails fast with that exact install command, so register such a site only once it is installed.

There are two ways you arrive here:

- **A known gated feed.** When the user already has the feed URL of a JS / anti-bot site, register it directly — `feed-filter add-site --id <id> --name <name> --feed-url <feed_url> --requires-browser` — and skip discovery.
  - Discovery fetches over plain HTTP and would itself be blocked by the gate, so it never runs for such a site and never produces a `needs_js` hint; the operator supplies the feed URL.
- **A JS-rendered scrape index.** Step 1's first `needs_js` case — an HTML page whose links did not cluster into articles — is the hint to retry a scrape site through the browser: register the page's own URL as `index_url`, add `--requires-browser`, and write the `article_url_pattern` yourself.
  - Discovery rejected, so there are no `sample_urls` and no synthesized pattern to choose between — step 2 does not apply, and [Writing the scrape pattern by hand](#writing-the-scrape-pattern-by-hand) has the shape yours must take.

The cold-start snapshot of a `requires_browser` site runs through the browser too, so the flood guard holds exactly as on the httpx path.
The anti-bot handling covers Cloudflare's first-line bot check only (it normalizes the headless User-Agent); a site that still serves an interactive challenge is unsupported and surfaces as a recurring per-site error at run time, not at registration.

### Writing the scrape pattern by hand

`article_url_pattern` is a Python regex, `re.search`ed against the **path** of each same-host link on the index page.
Scheme, host, query and fragment are all stripped before the match, so a pattern written against the whole URL matches nothing.
Discovery's own patterns are the shape to copy: article links at `/blog/<slug>` give `^/blog/[^/]+/?$`.
The plain fetch that would have shown you those paths is the one that just failed, so ask the user for two or three of the site's article URLs and anchor a pattern of that form on the prefix they share.

Check those URLs sit on the host the index URL **lands on** after redirects, which is what the same-host filter compares against.
A link off it is dropped before the regex ever sees it, so where the articles live on another host — `blog.example.com` under an `example.com` index — no pattern reaches them; register that host's own listing page as `index_url` instead, and tell the user where it has none.

Nothing then checks the pattern against the site.
`add-site` and `heal-site` both check only that it compiles, so `https://example.com/blog/.*` — a valid regex that no path can match — is accepted at exit 0 and the site then yields nothing.
A site yielding nothing shows as `snapshotted: 0` on the command you just ran, and as `zero_links` on every later run — and for a `requires_browser` site the run's self-heal cannot clear it, because self-heal re-derives the pattern by re-running discovery over plain HTTP, the same fetch that produced no pattern here.

Neither signal says why, and the causes are several: the pattern itself, a link the same-host filter dropped, or a list the browser missed because it captures the DOM at the load event and waits for no later render.
Report to the user what you registered, what came back, and the article URLs you worked from, rather than rewriting the regex against a cause you cannot see.

Repair a site that is already registered with `feed-filter heal-site --site-id <id> --pattern <corrected>`, never by re-running `add-site`.
`add-site` snapshots the back-catalog before it rejects the duplicate id, so re-running it marks that site's articles seen and still leaves the broken pattern in place.

## Optional per-site selection override

If the user wants different keep/drop criteria for this one site, set its `selection` field.
It overrides the **Topics** section of `prompts/selection.md` for that site only (see `prompts/selection.md`).
Set it at registration with `feed-filter add-site … --selection "<criteria>"`, or add/change it later by editing the `selection = "..."` line under that site's `[[site]]` block in `sites.toml` (user-authored config, so hand-editing is fine).
That file is gitignored personal state (see `packages/feed-filter/CLAUDE.md`), so no checkout restores a bad edit — report the old value with the change.

## Notes

- `sites.toml` is trusted, user-authored config — discovery applies no SSRF guard (SEC-001).
  - Only register URLs the user intends.
- Discovery is deterministic and network-bound; if it is slow, it is fetching candidate feed URLs, not hanging.
