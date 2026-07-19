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
| `shelved` | Checkbox, set by the **human**. "Read later": move the card to the Later view to look at when there is time. Always present (default `false`); feed-filter omits it on a re-write, so a shelved card stays shelved. |
| `dismissed` | Checkbox, set by the **human**. "Cleanable": hide the card from the Inbox as a future auto-cleanup target. Always present (default `false`). feed-filter **resets it to `false` on every write**, so a re-reminded topic (a new qualifying forum post) resurfaces into the Inbox rather than staying dismissed. |
| `wall` | Boolean, set by **feed-filter**. The page is behind a login or paywall, so it was admitted on its summary alone; surfaced in the Walls view for the human to judge. Always present (default `false`). |
| `feed_kind` | `article` or `forum` — which gather produced it (`kboat-feed-run` vs `kboat-forum-run`). |
| `site_id` | The registered site's id (from feed-filter's `sites.toml`); the provenance and grouping key. |
| `summary` | A short summary or snippet from the feed entry, in the language it came in. Empty when the source gave none. Lets a card be judged at a glance in the Base. |
| `added_date` | Date the note was filed into the vault. |

## Status and lifecycle

A feed note has no destructive routine action and no cooldown, so its lifecycle is entirely manual triage over three always-present booleans.

- `shelved` and `dismissed` are the **human's** two dispositions, orthogonal to each other:
  - `shelved` moves the card to the Later view — a "read later" holding shelf — without removing it from anywhere destructive. feed-filter preserves it across a re-write.
  - `dismissed` hides the card from the Inbox and marks it a future auto-cleanup target. Cleanup is **manual for now**: no note is auto-deleted, and the Base views only hide dismissed cards.
- `wall` is **feed-filter's** flag, re-evaluated on each write, not a human disposition.
- **Promotion is manual.** To read a feed card as a full K-Boat source, add its `url` to the `K-Boat Queue` (the `kboat-ingest` inbox) by hand; there is no auto-promotion from a feed note to a source note. The two are separate inboxes.
- **A re-write resurfaces the topic.** feed-filter writes an article item once (its seen-store de-dups), but a re-reminded forum topic — a new post crossing the like threshold — upserts the same note again. That re-write resets `dismissed` to `false`, so a topic the reader dismissed reappears in the Inbox when it gains new activity. `shelved` is instead preserved; feed-filter refreshes `wall`, `summary`, and the metadata.

`kboat-validate` checks every `Feeds/*.md` against the `FEED` schema (the generic per-field checks — presence, emptiness, kind/enum/date); the feed type carries no cross-field rules.

## Feeds Base

A standalone Base at the vault root, `Feeds.base`, over `type == "feed"`, gives the human four card views of the triage queue.
Every filter is a plain boolean over an always-present property, per the Base-authoring discipline in `kboat-vault-conventions` — never a `!=` over a possibly-missing property, never a date-emptiness test.
The file is hash-named, so each card leads with a `title_link` formula (the readable, clickable title) rather than the file name; the `url_link` formula opens the page in one click.

- **Inbox** (`dismissed != true && shelved != true`) — the working view, listed first so it is the default: the fresh, untriaged cards. A `wall` card still appears here (it is an untriaged keep); the `wall` property flags it.
- **Shelf** (`shelved`) — the read-later shelf.
- **Walls** (`wall`) — the focused subset admitted on their summary alone, for the human to judge whether the wall is worth clearing.
- **Dismissed** (`dismissed`) — the cleanable cards, kept visible here so a dismissal can be undone before any future cleanup.

```yaml
filters:
  and:
    - type == "feed"
formulas:
  title_link: file.asLink(note.title)
  url_link: link(url)
views:
  - type: cards
    name: Inbox
    filters:
      and:
        - dismissed != true
        - shelved != true
    order:
      - formula.title_link
      - summary
      - shelved
      - dismissed
      - site_id
      - added_date
      - wall
    sort:
      - property: added_date
        direction: DESC
    cardSize: 360
    rowHeight: medium
  - type: cards
    name: Shelf
    filters:
      and:
        - shelved
    order:
      - formula.title_link
      - summary
      - shelved
      - dismissed
      - site_id
      - added_date
      - wall
    sort:
      - property: added_date
        direction: DESC
    cardSize: 360
    rowHeight: medium
  - type: cards
    name: Walls
    filters:
      and:
        - wall
    order:
      - formula.title_link
      - formula.url_link
      - summary
      - shelved
      - dismissed
      - site_id
      - added_date
    sort:
      - property: added_date
        direction: DESC
    cardSize: 360
    rowHeight: medium
  - type: cards
    name: Dismissed
    filters:
      and:
        - dismissed
    order:
      - formula.title_link
      - summary
      - shelved
      - dismissed
      - site_id
      - added_date
      - wall
    sort:
      - property: added_date
        direction: DESC
    cardSize: 360
    rowHeight: medium
```

The views are `cards` (a gallery browse) at `cardSize: 360`; flip a view to a `table` in Obsidian's UI if you prefer that layout (the view type persists to the file, like the `cardSize`/`rowHeight` cosmetics the live Base carries).
Each card leads with the title and summary, then the two human triage checkboxes `shelved` and `dismissed`; `wall` sits last, apart from those two, because it is feed-filter's read-only flag, not something the reader ticks.
Card size and other cosmetics are per-vault tweaks.
