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
- Login walls, index/listing pages, or content that is not an article.
- <any topic you specifically do not want, e.g. "cryptocurrency price news">

## Heuristics

- Judge the article itself, not the site's general reputation — a good site still publishes off-topic pieces.
- A title alone can be misleading; if the title is ambiguous, read the body before deciding (see the run routine's two-stage judgment).
- Length is not quality: a short focused post can be worth keeping, a long SEO page is not.

## Output

Return a single JSON object and nothing else:

```json
{
  "keep": true,
  "title": "A concise, human-readable title for the page",
  "summary": "One sentence on what the page is about and why it was kept.",
  "reason": "One short clause explaining the keep/drop decision."
}
```

- `keep` — boolean.
- `title` — a non-empty title for the reminder. For scrape entries (no feed metadata) this is the **only** source of a title, so always supply one; on a keep it becomes the reminder's name.
- `summary` — one line; becomes the reminder's note. Omit or leave empty on a drop.
- `reason` — a brief justification, surfaced only in the run summary.
