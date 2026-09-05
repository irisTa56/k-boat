---
name: kboat-manage-feed-sites
description: Pause or resume a feed-filter site (disable/enable), point a site that moved at its new URL, and show which sites are currently paused vs active. Use when the user wants to disable/pause/stop a site, enable/resume one, fix a site whose feed/index/forum URL moved to a new domain or path, or check the on/off status of registered sites — e.g. after a recurring site error surfaces in a run's notification.
---

# Manage feed-filter sites (pause / resume / move / status)

Turn an individual site's gathering off or on without losing it, point one at a new URL after it moves, and report which sites are currently paused.
This is the ad-hoc, user-driven half of site management — registration lives in the `kboat-add-feed-site` skill, the periodic run in `kboat-feed-run`.

Run every `feed-filter` command from the repo root.
The `feed-filter` binary lives in the workspace venv, on `PATH` only after `eval "$(mise env)"`; a bare `feed-filter …` otherwise fails with `command not found`.
Each Bash call starts a fresh shell, so loading it once does not carry across calls — prefix every `feed-filter` command with `eval "$(mise env)" &&` (the first command below shows it; apply the same to every call).
Each subcommand emits one JSON document on stdout and exits non-zero on failure — parse the JSON and read the exit code.

## See what's paused

Run `eval "$(mise env)" && feed-filter list-sites`: it returns every site with an `enabled` field.
Report the sites with `enabled: false` as paused and the rest as active — e.g. "2 paused (Lab BRAINS, Foo Blog), 68 active".
This is the only place the on/off status lives; there is no separate state to consult.

## Pause a site

`feed-filter disable-site --site-id <id>` → `{site_id, enabled: false}`.
The run then skips it entirely — no fetch, no error, no notification — while its `[[site]]` config and its seen-store stay intact.
Use this when a site is chronically failing (a recurring error named in the run's summary), temporarily noisy, or simply unwanted for now.
Prefer it over deleting the `[[site]]` block by hand, which loses the row's own settings and, on an article site, changes what it does when it comes back: `add-site` records the current entries as seen, so everything published in the gap is skipped unjudged, where a resume judges it.

## Resume a site

`feed-filter enable-site --site-id <id>` → `{site_id, enabled: true}`.
Gathering resumes, and the preserved seen-store keeps the pre-pause back-catalog out, so there is no flood.
What the pause did not record still arrives, though: nothing was gathered while the site was disabled, so anything published during the pause is unseen, and whatever of it the feed or index still carries is judged on the first run back.
Say so when reporting a resume, rather than promising only post-resume entries.

## Fix a site that moved

A moved site — a new domain, a new subdomain, a renamed feed or index path — keeps its id, its config and its seen-store; only one URL is wrong.
So the fix is to change that one value, and never to disable the site.
No `feed-filter` subcommand edits a URL, and none is needed: `sites.toml` is the registry itself, and it is user-authored config that this skill edits by hand.

1. **Read the site's current row.** `feed-filter list-sites` reports its `id` and which of the three URL fields it carries — `feed_url`, `index_url`, or `forum_url`.
2. **Replace that field's value** under the site's `[[site]]` block in `packages/feed-filter/sites.toml`, leaving no second copy of the key behind.
   - Change the value only. Which of the three fields is set is what makes a site a feed, a scrape, or a forum site, and the registry loader rejects a row that sets anything other than exactly one of them (`index_url` also requires an `article_url_pattern` beside it).
   - Note the old value in the run's report before overwriting it. `sites.toml` is gitignored personal state rather than version-controlled config (see `packages/feed-filter/CLAUDE.md`), so no checkout restores a bad edit.
3. **Confirm the registry still loads, and that the site is enabled.** Run `feed-filter list-sites` again — it parses every row, so it fails on a bad row anywhere in the file, and its output is where you verify the new URL took.
   Where an earlier escalation read the move as a dead site and disabled it, re-enable it now: a disabled site gathers nothing, so it can never raise the error that would bring it back to anyone's attention.
4. **Report what the move cost.** Where it changed the URLs of what the site serves, those URLs read as new: an article is judged a second time, and a keep — an article's or a topic's — lands as a fresh `Feeds/` note instead of updating the one already there.
   On a scrape site `feed-filter heal-site --site-id <id> --pattern <the pattern list-sites reports>` re-scrapes under the new `index_url` and records what it finds as seen, which ends the re-judging at once; on a feed site it ends once those entries have each been judged, at most twenty in a run.
   Either way the move is not free, so do not report it as such.

## Finding the id

The user usually names a site by its title or URL, not its `id`.
Run `feed-filter list-sites`, match the user's reference against each site's `name` / `feed_url` / `index_url`, and use that site's `id`.
A non-zero exit from `disable-site` / `enable-site` means the id was not found — re-check it against `list-sites`.
