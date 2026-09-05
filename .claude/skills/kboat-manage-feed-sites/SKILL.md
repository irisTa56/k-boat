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
What the pause did not record still arrives, though: nothing was gathered while the site was disabled, so anything published during the pause is unseen, and whatever of it the feed or index still carries is judged over the runs after it.
Say so when reporting a resume, rather than promising only post-resume entries.

## Fix a site that moved

A moved site — a new domain, a new subdomain, a renamed feed path — keeps its id and its config; what is wrong is the URL it is registered under.
So the fix is to change that value, and never to disable the site.
No `feed-filter` subcommand edits a URL, and none is needed: `sites.toml` is the registry itself, and it is user-authored config that this skill edits by hand.
What goes in is a URL confirmed to serve this same site, never one inferred from the error page that raised the suspicion — where the move is only suspected, report the candidate and leave the row alone.

1. **Read the site's current row.** `feed-filter list-sites` reports its `id` and which of the three URL fields it carries — `feed_url`, `index_url`, or `forum_url`.
2. **Replace that field's value** under the site's `[[site]]` block in `packages/feed-filter/sites.toml`, leaving no second copy of the key behind.
   - Change the value only, and leave the row's other fields where they are.
     A row's kind comes from which of `feed_url`, `article_url_pattern` and `forum_url` it sets — the loader takes exactly one and rejects anything else — while `index_url` is not one of those three and is instead required alongside `article_url_pattern`.
   - Give the user the old value when you report the change.
     `sites.toml` is gitignored personal state rather than version-controlled config (see `packages/feed-filter/CLAUDE.md`), so no checkout restores a bad edit and that report is the only record of what it said.
3. **Confirm the registry still loads, and that the site is enabled.** Run `feed-filter list-sites` again — it parses every row, so it fails on a bad row anywhere in the file, and its output is where you verify the new URL took.
   Where an earlier escalation read the move as a dead site and disabled it, re-enable it now: a disabled site gathers nothing, so it can never raise the error that would bring it back to anyone's attention.

That is the whole of the fix — nothing else has to be run, and what the next runs then do with the site is `kboat-feed-run`'s and `kboat-forum-run`'s to say, not this skill's.

## Finding the id

The user usually names a site by its title or URL, not its `id`.
Run `feed-filter list-sites`, match the user's reference against each site's `name` / `feed_url` / `index_url` / `forum_url`, and use that site's `id`.
A non-zero exit from `disable-site` / `enable-site` means the id was not found — re-check it against `list-sites`.
