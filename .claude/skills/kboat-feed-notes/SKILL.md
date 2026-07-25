---
name: kboat-feed-notes
description: Conventions for the feed-filter member's Feeds vault notes. Use when creating or updating a Feeds note, authoring the Feeds Base, or when you need the exact FEED frontmatter schema, the shelved/dismissed/wall status semantics, or the feed triage lifecycle. This is the source of truth for the feed note type and its lifecycle; the shared vault mechanics (URL-hash naming, the schema/validate contract, the write contract, Base discipline) are the kboat-vault-conventions skill, which this defers to. The kboat-feed-run and kboat-forum-run skills defer here for the note format.
---

# Feed note conventions

The feed-filter member triages new pages from registered feeds and Discourse forums, and files the survivors into the K-Boat Obsidian vault as **feed notes** (`Feeds/*.md`).
A feed note is a lightweight triage card — a `type: feed` note, frontmatter only, no body — parallel to but far simpler than a K-Boat source note: there is no NotebookLM notebook, no distillation, and no cooldown.
It is a "here is something worth a look" entry the human browses, shelves, dismisses, or promotes to a full K-Boat read.

This skill owns the feed note *type* and its lifecycle.
The shared vault mechanics it relies on — URL-hash naming, the code-authoritative `kboat.schema`, the `kboat.write.upsert` write contract, and Base-authoring discipline — are the `kboat-vault-conventions` skill; read it for those.

## Feed note (`Feeds/*.md`)

Frontmatter only, no body.
The note is named by the URL hash of its `url` (the `kboat-vault-conventions` recipe); feed-filter hashes its **canonical** URL, so URL variants of one page collapse to a single note (its own de-dup key), and the readable title lives in the `title` property, surfaced by the Base.
The write is owned by `kboat.write.upsert` (schema `FEED`), which feed-filter calls directly in Python — field order, YAML quoting, the always-present boolean defaults, de-dup by `url`, and the `added_date` stamp are guaranteed rather than hand-assembled.

| Property | Meaning |
| --- | --- |
| `type` | Always `feed`. |
| `title` | The entry or topic title. |
| `url` | The page's canonical URL — the read link and the de-dup identity. Immutable. |
| `shelved` | Checkbox, set by the **human**. "Read later": move the card to the Shelf view to look at when there is time. Always present (default `false`); feed-filter omits it on a re-write, so a shelved card stays shelved. |
| `dismissed` | Checkbox, set by the **human**. "Cleanable": hide the card from the Inbox as a future auto-cleanup target. Always present (default `false`). feed-filter **resets it to `false` on every write**, so a re-reminded topic (a new qualifying forum post) resurfaces into the Inbox rather than staying dismissed. |
| `wall` | Boolean, set by **feed-filter**. The page is behind a login or paywall, so it was admitted on its summary alone; surfaced in the Walls view for the human to judge. Always present (default `false`). |
| `feed_kind` | `article` or `forum` — which gather produced it (`kboat-feed-run` vs `kboat-forum-run`). |
| `site_id` | The registered site's id (from feed-filter's `sites.toml`); the provenance and grouping key. |
| `summary` | A short summary or snippet from the feed entry, in the language it came in. Empty when the source gave none. Lets a card be judged at a glance in the Base. |
| `added_date` | Date the note was filed into the vault. |

## Status and lifecycle

A feed note has no destructive routine action and no cooldown, so its lifecycle is entirely manual triage over three always-present booleans.

- `shelved` and `dismissed` are the **human's** two dispositions; `dismissed` takes precedence over `shelved`, hiding the card from both working views:
  - `shelved` moves the card to the Shelf view — a "read later" holding shelf — without removing it from anywhere destructive. feed-filter preserves it across a re-write.
  - `dismissed` hides the card from both the Inbox and the Shelf — a dismissed card leaves the working views whether or not it is shelved — and marks it a future auto-cleanup target. Cleanup is **manual for now**: no note is auto-deleted; the Base only hides dismissed cards from the working views and keeps them in the Dismissed view so a dismissal can be undone.
- `wall` is **feed-filter's** flag, re-evaluated on each write, not a human disposition.
- **Promotion is manual.** To read a feed card as a full K-Boat source, capture its `url` into the `Queue/` folder that `kboat-ingest` drains (via the capture bookmarklet, or by hand); there is no auto-promotion from a feed note to a source note. The two are separate inboxes.
- **A re-write resurfaces the topic.** feed-filter writes an article item once (its seen-store de-dups), but a re-reminded forum topic — a new post crossing the like threshold — upserts the same note again. That re-write resets `dismissed` to `false`, so a topic the reader dismissed reappears in the working views when it gains new activity. `shelved` is instead preserved; feed-filter refreshes `wall`, `summary`, and the metadata.

`kboat-validate` checks every `Feeds/*.md` against the `FEED` schema (the generic per-field checks — presence, emptiness, kind/enum/date); the feed type carries no cross-field rules.

## Feeds Base

A standalone Base at the vault root, `Feeds.base`, over `type == "feed"`, gives the human four table views of the triage queue.
Every filter is a plain boolean over an always-present property, per the Base-authoring discipline in `kboat-vault-conventions` — never a `!=` over a possibly-missing property, never a date-emptiness test.

The column set is driven by the triage motion: read the summary, open the page when it looks worth it, tick a checkbox.
Every step has to be reachable without scrolling sideways, so a view carries only what that motion needs — the card, and the two checkboxes.

The lead column is a `title_summary` formula that folds the whole card into one cell and makes that cell the link to the page.
[Both `link()` and `file.asLink()` take display text as an argument](https://help.obsidian.md/bases/functions), so a whole card can serve as the anchor text of a single link — built inside that argument, since in practice concatenating a Link with a string renders as plain text.
The formula points at `url` rather than at the note's own file because a feed note has no body: `site_id` and `feed_kind` are provenance the reader does not weigh at triage time, so there is nothing behind the row worth opening.
The 💬 separator is an emoji on purpose: a plain punctuation separator could itself occur inside a title or summary and blur the boundary, which an emoji will not.
`wall` rides in the same cell as a 🔒 prefix rather than spending a column of its own: a glyph on the card costs no horizontal room, and being feed-filter's read-only flag it is not a third checkbox either.
The other two columns are the human's triage checkboxes, `dismissed` first (the more frequent action) then `shelved`.
`added_date` orders the rows without being displayed.

- **Inbox** (`dismissed != true && shelved != true`) — the working view, listed first so it is the default: the fresh, untriaged rows. A `wall` row still appears here (it is an untriaged keep); the 🔒 prefix flags it.
- **Shelf** (`shelved && dismissed != true`) — the read-later shelf; a card the reader later dismisses drops out of here too, since `dismissed` hides from both working views.
- **Walls** (`wall`) — the focused subset admitted on their summary alone, for the human to judge whether the wall is worth clearing. It is a flag view rather than a working view, so unlike the Inbox and the Shelf it does not exclude dismissed rows.
- **Dismissed** (`dismissed`) — the cleanable rows, kept visible here so a dismissal can be undone before any future cleanup.

```yaml
filters:
  and:
    - type == "feed"
formulas:
  title_summary: link(url, if(wall, "🔒 ", "") + note.title + " 💬 " + note.summary)
views:
  - type: table
    name: Inbox
    filters:
      and:
        - dismissed != true
        - shelved != true
    order:
      - formula.title_summary
      - dismissed
      - shelved
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Shelf
    filters:
      and:
        - shelved
        - dismissed != true
    order:
      - formula.title_summary
      - dismissed
      - shelved
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Walls
    filters:
      and:
        - wall
    order:
      - formula.title_summary
      - dismissed
      - shelved
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Dismissed
    filters:
      and:
        - dismissed
    order:
      - formula.title_summary
      - dismissed
      - shelved
    sort:
      - property: added_date
        direction: DESC
```

The views are `table`.
The lead column's width and the row height are tuned together — the column narrow enough that both checkboxes fit beside it, the row tall enough that the narrowed card still shows in full — but both are per-vault tweaks, so the live `Feeds.base` carries a `columnSize` and `rowHeight: extra` not shown here.
