# Selection criteria

This file is the keep/drop prompt the run routine hands to each judging subagent.
A subagent sees one candidate page at a time and decides whether it belongs in the **`Filtered Feeds`** Reminders list.
Edit the criteria below to match what you actually want to read; the routine reloads this file every run, so changes take effect on the next run with no code change.

A per-site override may live in `sites.toml` (`selection = "..."` on a `[[site]]`); when present it replaces the **Topics** section below for that site only, while the **Output** and **Heuristics** sections still apply.

## Decision

Return `keep = true` only when the page is something you would genuinely want to read later.
When in doubt, prefer to **keep** — a stray extra reminder is cheaper to dismiss than a missed article is to recover (the seen-store never re-surfaces a dropped entry).

## Topics

> Replace the placeholders below with your own interests. These are examples, not defaults.

**Keep** pages that are about:

- <topic you want to follow, e.g. "practical machine-learning engineering">
- <another topic, e.g. "distributed systems design write-ups">

**Drop** pages that are:

- Pure marketing, release-note churn, or job postings.
- Index/listing pages, or content that is not an article.
- <any topic you specifically do not want, e.g. "cryptocurrency price news">

## Heuristics

- Judge the article itself, not the site's general reputation — a good site still publishes off-topic pieces.
- A title alone can be misleading; if the title is ambiguous, read the body before deciding (see the run routine's two-stage judgment).
- Length is not quality: a short focused post can be worth keeping, a long SEO page is not.

## Walls and unreadable pages

Sometimes the fetched page is not the article but a login wall, paywall, or subscribe gate — you got a "sign in / subscribe" page instead of the body.
You cannot judge an article you cannot read, so do **not** drop it on a guess: set `wall = true`.
When `wall = true` the run routine reminds the page for manual review and **ignores `keep`**, so `keep` is irrelevant — set it either way.
Wall detection only happens once you actually fetch the page: scrape entries are always fetched, but a feed entry you can judge from its title and summary alone is judged normally and never reaches a wall — its metadata is the readable content, so there is nothing to flag.
Reserve a wall for a page you would plausibly **keep** if you could read it.
If the title and summary already place the page **outside the Topics**, drop it on that basis — do not fetch an out-of-scope page just to assess its depth, so it cannot become a needless manual-review reminder.
Escalate to the body (and thus, for a gated site, a wall) only when the page is plausibly in-scope and only its quality or depth is unresolved.
Hard fetch errors (the page would not load at all) are handled by the run routine itself, not here.

## Output

Return a single JSON object and nothing else:

```json
{
  "keep": true,
  "wall": false,
  "title": "A concise, human-readable title for the page",
  "summary": "One sentence on what the page is about and why it was kept.",
  "reason": "One short clause explaining the keep/drop decision."
}
```

- `keep` — boolean.
- `wall` — boolean; `true` when the fetched page is a login wall / paywall / subscribe gate rather than the article (see "Walls and unreadable pages"). A walled page is reminded for manual review regardless of `keep`.
- `title` — a non-empty title for the reminder. For scrape entries (no feed metadata) this is the **only** source of a title, so always supply one; on a keep it becomes the reminder's name.
- `summary` — one line; becomes the reminder's note. Omit or leave empty on a drop.
- `reason` — a brief justification, surfaced only in the run summary.
