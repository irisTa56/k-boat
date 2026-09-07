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

   - **Non-zero exit** → discovery did not complete.
     - Relay the error line as it stands and stop, rather than naming a cause: it says whether an HTTP status came back, and nothing about why.
   - **`rejection` is set** (exit 0, no usable candidate) → take the next step from `reason` alone and never from the `message` wording, and relay that message to the user instead of proceeding:
     - `needs_js` → an HTML page whose links did not cluster into articles.
       - A JavaScript-rendered index is the common cause, and an anti-bot interstitial served as an ordinary page reaches it too, so do not relay JavaScript to the user as the established cause.
       - Either ask for a server-rendered alternative URL (a feed link or a plain archive page), or — if the user wants this exact page as a scrape site — retry registration through the opt-in browser path with `--requires-browser` (see "Sites that need a browser" below).
     - `no_article_clusters` → no feed and no article-shaped link cluster was found.
       - Ask the user to point at the site's article-listing/archive page (e.g. `/blog`, `/posts`, `/news`) rather than its landing page, and re-run discovery on that.
     - `no_html_body` → a body with nothing in it, or one the server did not label as HTML, so discovery stopped before the clustering step and has established nothing about the page's article links.
       - Ask the user for a URL that serves the site's articles as HTML — its article-listing page, or a feed — and re-run discovery on that.
       - Where they say the URL they gave already is that page, stop and report that the response it returns carries nothing discovery can read articles from, rather than asking again.
     - `unparseable_body` → the body had content and was labelled HTML, but the parser refused it, so discovery established nothing about the page's article links.
       - Ask the user for a different URL that serves the site's articles — its article-listing page, or a feed — and re-run discovery on that.
       - Where they have none, report that this URL cannot be registered from what discovery has, and stop: the rejection carries no candidate, so there is no `article_url_pattern` to register with, and `add-site` rejects a scrape site that has none.
       - Never write a pattern of your own here: the parser refused the body, so nothing is known about the page's links to write one from.
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
   - **Scrape:** `feed-filter add-site --id <id> --name <name> --index-url <index_url> --article-url-pattern '<article_url_pattern>'`
     - Quote the pattern, so the shell does not take its backslashes out — the mangled regex still compiles, so the site registers and matches nothing.
     - A pattern taken from discovery's output arrives JSON-doubled and has to be unescaped as well; one you wrote yourself is already the value to pass.
   - Append `--requires-browser` for a JS / anti-bot site (see "Sites that need a browser" below).

   `add-site` snapshots the site's **current** entries into the seen-store **first** (durably), then writes `sites.toml` **last**.
   That snapshot is the cold-start flood guard: only entries that appear *after* registration are ever written as notes.
   A non-zero exit *before* that snapshot — the back-catalog fetch failing — leaves nothing written, so report it and retry.
   One after it does not: the id is checked only when `sites.toml` is written, so re-running `add-site` on a site that already exists exits non-zero with that site's articles freshly marked seen.

5. **Confirm.** On success the output is `{site_id, kind, snapshotted}`.
   - Tell the user the site was registered, its `kind` (feed or scrape), and how many existing entries were snapshotted as already-seen (so they understand nothing from the back-catalog will be written as a note).

## Registering a Discourse forum

A Discourse forum is registered with `add-forum`, not `add-site`.
There is **no discovery** (there is no article cluster to pick) and **no cold-start snapshot**: forum topics are admitted at poll time, so a snapshot would silently discard every topic due for first-run Rule-A judgment — a loss, not a flood guard.

1. **Confirm it is a Discourse forum.** Unless the user has already made that clear, verify the instance serves `<forum_url>/latest.rss` (a Discourse RSS feed) before registering.
   - `add-forum` validates only the config shape, so a non-Discourse URL registers cleanly but then fails every run with a per-site fetch error.
   - A quick `WebFetch` of the forum's landing page (or `/latest.rss`) both confirms Discourse and gives you the subject for the next step.

2. **Choose an id and name** — same rules as the article path: `--id` a short, stable, unique slug (check `feed-filter list-sites` for collisions), `--name` a human-readable label.

3. **Pick the native subject (`--forum-subject`).** This is the forum's own domain — e.g. `Erlang` for `erlangforums.com`.
   - It is excluded as a Rule-A match reason so the run keeps only topics interesting *outside* this forum's community, not the on-subject ones.
   - Infer it from the forum's title/description, or ask the user.
   - Omit it for a general-interest forum with no single subject (Rule A then judges on interest alone).

4. **Optional tuning** — each flag defaults to the value in `config.py`, so pass only what the user wants to change:
   - `--like-threshold N` (default 6) — Rule-B like bar when Rule A dropped the topic.
   - `--interest-like-threshold N` (default 3) — Rule-B like bar when Rule A kept the topic.
   - `--daily-watch-count N` (default 3) / `--weekly-watch-count N` (default 5) — how many daily / weekly top topics to watch.
   - `--poll-offsets-days N [N …]` (default `0 1 7`) — days from first-seen at which to poll; the topic retires after the last offset.

5. **Register.** `feed-filter add-forum --id <id> --name <name> --forum-url <forum_url> [--forum-subject <subject>] [tuning…]`.
   - This writes `sites.toml` only — no snapshot.
   - A non-zero exit means the arguments were rejected — a missing or malformed flag (argparse), or a config-shape error from `SiteConfig`; fix the args and retry.

6. **Confirm.** On success the output is `{site_id, kind, forum_url}` with `kind == "forum"`.
   - Tell the user the forum was registered and that keeps will be written as `Feeds/` notes in the vault (`feed_kind: forum`).
   - Nothing else is needed: the `Feeds/` folder is created on the first write, and the run only needs `OBSIDIAN_VAULT_PATH` set (from the workspace `.env`).

A forum's per-site `selection` override is not an `add-forum` flag.
Set it later by hand-editing the `selection = "..."` line under that forum's `[[site]]` block in `sites.toml` (the forum run honors it, replacing the Topics section for that forum only).
That file is gitignored personal state (see `packages/feed-filter/CLAUDE.md`), so no checkout restores a bad edit — report the old value with the change.

## Sites that need a browser (JS / anti-bot)

A site that renders its feed/index with JavaScript, or gates it behind an anti-bot challenge such as Cloudflare, is registered through the opt-in browser path by adding `--requires-browser` to `add-site`.
The flag needs the optional Playwright extra (`uv sync --extra browser && uv run playwright install chromium`); without it the command fails fast with that exact install command, so register such a site only once it is installed.

There are two ways you arrive here:

- **A known gated feed.** When the user already has the feed URL of a JS / anti-bot site, register it directly — `feed-filter add-site --id <id> --name <name> --feed-url <feed_url> --requires-browser` — and skip discovery.
  - Discovery looks for exactly what you already have, so running it here adds nothing.
- **A JS-rendered scrape index.** Step 1's `needs_js` rejection is the hint to retry a scrape site through the browser: register the page's own URL as `index_url`, add `--requires-browser`, and write the `article_url_pattern` yourself.
  - Discovery rejected, so there are no `sample_urls` and no synthesized pattern to choose between — step 2 does not apply, and [Writing the scrape pattern by hand](#writing-the-scrape-pattern-by-hand) has the shape yours must take.

The cold-start snapshot of a `requires_browser` site runs through the browser too, so the flood guard holds exactly as on the httpx path.
The anti-bot handling covers Cloudflare's first-line bot check only (it normalizes the headless User-Agent); a site that still serves an interactive challenge is unsupported and surfaces as a recurring per-site error at run time, not at registration.

### Writing the scrape pattern by hand

`article_url_pattern` is a Python regex, `re.search`ed against the **path** of each same-host link on the index page.
That path is canonicalized first: scheme, host, query and fragment are stripped, duplicate slashes collapse, percent-escapes are upper-cased, and the trailing slash is dropped from everything but the root.
So a pattern written against the whole URL matches nothing, and neither does one that requires a trailing slash — however the site writes its permalinks, the regex never sees one.
Where the article's identity is in the query rather than the path — `?p=123`, `index.php?post=<slug>` — no pattern reaches it: the query is stripped, so every article canonicalizes to the same path and one entry stands for all of them.
That site cannot be scraped by pattern at all, so report it back rather than registering it; `snapshotted` comes back non-zero and `zero_links` never fires, so nothing later would tell you.

A non-ASCII segment is the other way to miss: the regex sees what the `href` holds, usually percent-encoded and now upper-cased, while the URL a user reads off their address bar is decoded.
Neither spelling is safe on its own, so write both — `^/(記事|%E8%A8%98%E4%BA%8B)/[^/]+$` matches under either — rather than generalizing the segment to `[^/]+`, which drops the one literal telling articles from navigation.
Discovery's own patterns are the shape to copy: article links at `/blog/<slug>` give `^/blog/[^/]+/?$`, with the per-article segment generalized to `[^/]+` rather than taken from any one URL.
Its fetch returned the page but not the article links, so it cannot show you their paths — ask the user for two or three of the site's article URLs and write a pattern of that form.
Keep the segments that are the same for every article, and generalize each one that varies — a slug to `[^/]+`, a date to `\d{4}/\d{2}/\d{2}`.
Anchor both ends, since the pattern is `re.search`ed rather than matched against the whole path: unanchored, `/blog/[^/]+` also takes `/blog/tags/python`, `/blog/page/2` and `/category/blog/roundup`.
A prefix the samples happen to share is not the same thing as a fixed segment: two posts from one month share `/2024/03`, and a pattern anchored there registers cleanly and then stops matching when the month rolls.

Check those URLs sit on the host the index URL **lands on** after redirects, which is what the same-host filter compares against — step 1's rejection message names that host, so you already have it.
What the filter actually reads is the host in each `href` the page carries, which a URL the user copied from their address bar need not match, and on this path you cannot see the page to settle it — so ask the user which host their article links are written under rather than inferring it from the URLs they gave.
A link off it is dropped before the regex ever sees it, and the hostnames are compared for exact equality — so `www.example.com` is off an `example.com` index as surely as `blog.example.com` is.
The `www` split is the one to look for, because it is the one you answer yes to: an index reached at the apex whose page writes absolute `www` permalinks matches nothing, and reads as a bad regex.
For the `www` split the repair is the same page under the right host: register the listing page as the links spell it, path and all, rather than the bare host — a homepage as `index_url` matches whatever few posts it happens to feature and reports a clean site.
Where the articles really do live on another site, no pattern reaches them at all: find that site's own listing page and register that, and tell the user where it has none.

Nothing then checks the pattern against the site.
`add-site` and `heal-site` both check only that it compiles, so `https://example.com/blog/.*` — a valid regex that no path can match — is accepted at exit 0 and the site then yields nothing.
A site yielding nothing shows as `snapshotted: 0` on the command you just ran, and as `zero_links` on every later run.
The run's self-heal will not clear either signal for a `requires_browser` site: it re-derives the pattern by re-running discovery, which reads over plain HTTP and so is not reading the page the gather reads, and the run reports such a site rather than healing it.

Neither signal says why.
The pattern is one cause among several — a link the same-host filter dropped is another, and so is a list rendered after the browser's load-event capture — and nothing bounds that set, so it is not a diagnosis to work through.
Report to the user what you registered, what came back, and the article URLs you worked from, rather than rewriting the regex against a cause you cannot see.

A pattern that matches too much has no signal at all: `snapshotted` is non-zero, `zero_links` never fires, and the count itself reads nothing, since it takes every matching link on the page and a "recent posts" or "popular" sidebar carries real article links too.
The flood guard hides the rest — everything the pattern took at registration is snapshotted seen, so the junk that ever reaches a judge is what appears afterwards, a new tag page or the next pagination link.
Report the pattern you registered and the count it snapshotted along with the rest, and leave the reading of that count to the user, who can see the page.

Repair a **pattern** on a site that is already registered with `feed-filter heal-site --site-id <id> --pattern '<corrected>'`, and with nothing else.
A wrong `index_url` is not a pattern, and `heal-site` cannot reach it — its parser takes only `--site-id` and `--pattern`, so no correction it accepts fixes that site.
Report that case rather than reaching for a command.
`heal-site` is the only path that snapshots the newly-matched URLs before it rewrites the config, so hand-editing `article_url_pattern` in `sites.toml` — which the registry otherwise invites, and which nothing stops you doing — leaves the whole live index unseen, and the next runs judge it a capful at a time and write the keeps as notes.
Where such a hand-edit has already happened, `feed-filter resnapshot-site --site-id <id>` staunches it: it marks what the stored pattern matches seen and touches no config.
Buries them, rather — see below — so it is the user's call and never yours; `kboat-manage-feed-sites` states the trade.
Re-running `add-site` is the other trap: it snapshots the back-catalog before it rejects the duplicate id, so it marks that site's articles seen and still leaves the broken pattern in place.
That snapshot cuts both ways, which is why the correction has to be one you can defend rather than the next guess: `heal-site` and `resnapshot-site` alike mark everything the pattern matched as seen with no note, so an over-broad one burns the whole live index and those articles are never written.

## Optional per-site selection override

If the user wants different keep/drop criteria for this one site, set its `selection` field.
It overrides the **Topics** section of `prompts/selection.md` for that site only (see `prompts/selection.md`).
Set it at registration with `feed-filter add-site … --selection "<criteria>"`, or add/change it later by editing the `selection = "..."` line under that site's `[[site]]` block in `sites.toml` (user-authored config, so hand-editing is fine).
That file is gitignored personal state (see `packages/feed-filter/CLAUDE.md`), so no checkout restores a bad edit — report the old value with the change.

## Notes

- `sites.toml` is trusted, user-authored config — discovery applies no SSRF guard (SEC-001).
  - Only register URLs the user intends.
- Discovery is deterministic and network-bound; if it is slow, it is fetching candidate feed URLs, not hanging.
