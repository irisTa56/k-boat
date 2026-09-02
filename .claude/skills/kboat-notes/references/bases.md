# Bases

Each grant below is the spec, not the file on disk: the live `.base` carries per-vault cosmetics besides — column widths, and the `columnSize` block `Kindles.base` has.

- [Sources Base](#sources-base)
- [Kindle Base](#kindle-base)
- [Repo Base](#repo-base)
- [Review Base](#review-base)

## Sources Base

A single standalone Base at the vault root, `Sources.base`, gives seven views over all sources: a **Today** view (the daily-pick shortlist plus what you are mid-read), three to-read inboxes — a **Web** view, an **All** view, and a **PDF** view — a **Holding** view of every filed source, an **Ambiguous** view of contradictory dispositions, and a **DLQ** view of sources ingest could not complete. The **Today** view is listed first so it is the default Obsidian opens (a Base shows its first view on open, as the [Kindle Base](#kindle-base) notes).
The Today view is the reading entry point, mobile included: it filters `distill != true && keep != true && dismiss != true && blocked != true` and then `picked == true || reading == true`, so it shows the day's at-most-two web picks (set by the daily-pick step, see [Daily pick](daily-pick.md#daily-pick)) next to every source you have started (`reading`) but not yet filed, whatever its `source_type`. Both halves are plain booleans, staying within the filter rules below; it carries `summary` so the two picks are legible at a glance, and sorts by `added_date` newest-first like the other inboxes (`picked` is hidden, so it is not a stable sort key).
The to-read inboxes filter `distill != true && keep != true && dismiss != true && blocked != true` — readable, undispositioned sources only (a blocked source has no content to read, so it belongs in the DLQ, not the inbox). The All inbox adds no type filter, so it is exhaustive over that set: every readable, undispositioned source appears whatever its `source_type`. The Web and PDF inboxes (`source_type ==`) are focused subsets, since web pages and PDFs are read differently — a URL versus Obsidian's PDF++. Do not replace All with a `source_type !=` catch-all: Obsidian Bases excludes a missing property from a `!=` filter, so a source lacking `source_type` would vanish.
The Holding view (`(distill || keep || dismiss)` and `blocked != true`) is where every filed source lives: the read-later shelf (`keep`), the cooldown window for `distill`/`dismiss` (change the disposition here before the routine processes it), and the processed/terminal states. It leads with the three disposition checkboxes plus `reading`, and carries `summary` for browsing along with `filed_date`/`distilled_date`/`notebooklm_id`, so each lifecycle state is legible from its columns. It is deliberately one view — the disposition booleans in the columns distinguish the states, so separate Shelf and Processed views are unnecessary.
The Ambiguous view (`dismiss && (keep || distill)`, and `blocked != true`) lists the contradictory sources the routine refuses to process, so they can be fixed. It is kept separate from Holding because it is an error state, not a resting one; like every non-DLQ view it excludes `blocked`, so a blocked source never leaks out of the DLQ.
The DLQ view (`blocked`) lists the sources ingest could not complete, with their `file.name` (the URL-hash slug) as the first column so it is easy to copy into `kboat-rescue`, plus the `url`; the failure is implied by their presence here. Rescuing one clears `blocked`, moving it out of the DLQ.
Every filter here is a plain boolean (`distill`, `keep`, `dismiss`, `blocked`) or an `==`/`!=` over an always-present property (`source_type` and the disposition booleans), per the Base-authoring discipline in `kboat-vault-conventions`; those booleans are written on every source at creation, so the views stay complete.
The to-read and Holding views lead with the disposition checkboxes and sort by `added_date`. The Web and PDF inboxes are single-type, so they omit the `source_type` column that the All and Holding views keep.

Because the filename is an opaque URL hash, the readable title is shown through a `title_link` formula — `file.asLink(note.title)` renders the `title` as text but links to the note, so a click opens the (hash-named) file. All views show `formula.title_link` in place of `file.name`.

```yaml
formulas:
  title_link: "file.asLink(note.title)"
filters:
  and:
    - type == "source"
views:
  - type: table
    name: Today
    filters:
      and:
        - distill != true
        - keep != true
        - dismiss != true
        - blocked != true
        - or:
            - picked == true
            - reading == true
    order:
      - reading
      - distill
      - keep
      - dismiss
      - formula.title_link
      - summary
      - source_type
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Sources · Web
    filters:
      and:
        - distill != true
        - keep != true
        - dismiss != true
        - blocked != true
        - source_type == "web_page"
    order:
      - reading
      - distill
      - keep
      - dismiss
      - formula.title_link
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Sources · All
    filters:
      and:
        - distill != true
        - keep != true
        - dismiss != true
        - blocked != true
    order:
      - reading
      - distill
      - keep
      - dismiss
      - formula.title_link
      - source_type
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Sources · PDF
    filters:
      and:
        - distill != true
        - keep != true
        - dismiss != true
        - blocked != true
        - source_type == "pdf"
    order:
      - reading
      - distill
      - keep
      - dismiss
      - formula.title_link
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Holding
    filters:
      and:
        - blocked != true
        - or:
            - distill
            - keep
            - dismiss
    order:
      - reading
      - distill
      - keep
      - dismiss
      - formula.title_link
      - summary
      - source_type
      - added_date
      - filed_date
      - distilled_date
      - notebooklm_id
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Ambiguous
    filters:
      and:
        - blocked != true
        - dismiss
        - or:
            - keep
            - distill
    order:
      - distill
      - keep
      - dismiss
      - formula.title_link
      - source_type
      - url
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: DLQ
    filters:
      and:
        - blocked
    order:
      - file.name
      - formula.title_link
      - source_type
      - url
      - added_date
    sort:
      - property: added_date
        direction: DESC
```

## Kindle Base

A standalone Base at the vault root, `Kindles.base`, over `type == "kindle"`, with three views. It leads with **Reading list**, the books not yet finished (`finished != true`) — the active to-read / now-reading shelf, so checking `finished` drops a book off it while leaving it in the All catalogue, and the list shrinks as books are read. Reading list is listed first deliberately: a Base shows [its first view on open](https://help.obsidian.md/bases/views), so the first view is the default, and the day-to-day working view should be the default. The other two are the **All** catalogue and a **To distill** view (`distill == true`). The To-distill view carries a `distilled_date` column so a distilled book (date set) can be told from a still-ripe one (date empty) — the filter cannot test date-emptiness, so the column carries that signal, as the source Holding view does; the All catalogue omits it. Titles show through a `title_link` formula (`file.asLink(note.title)`) because the file is named by ASIN.

```yaml
filters:
  and:
    - type == "kindle"
formulas:
  title_link: file.asLink(note.title)
views:
  - type: table
    name: Reading list
    filters:
      and:
        - finished != true
    order:
      - reading
      - finished
      - distill
      - formula.title_link
      - author
      - published
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: Kindles · All
    order:
      - reading
      - finished
      - distill
      - formula.title_link
      - author
      - published
      - added_date
    sort:
      - property: added_date
        direction: DESC
  - type: table
    name: To distill
    filters:
      and:
        - distill
    order:
      - reading
      - finished
      - distill
      - formula.title_link
      - author
      - distilled_date
      - added_date
    sort:
      - property: added_date
        direction: DESC
```

## Repo Base

A standalone Base at the vault root, `Repos.base`, over `type == "repo"`, with an **All** catalogue plus focused **By role** / **By domain** style views. Titles show through a `title_link` formula because the file is hash-named, and a `url_link` formula makes the GitHub URL clickable. Every filter is a plain boolean or an `==` over an always-present property (`role`, `status`, `archived`) — never a date-emptiness test, the same rule the Sources Base follows.

```yaml
filters:
  and:
    - type == "repo"
formulas:
  title_link: file.asLink(note.title)
  url_link: link(url)
views:
  - type: table
    name: Repos · All
    order:
      - reading
      - formula.title_link
      - role
      - language
      - domain
      - status
      - stars
      - last_commit
      - added_date
    sort:
      - property: stars
        direction: DESC
  - type: table
    name: Active
    filters:
      and:
        - status == "recent"
    order:
      - formula.title_link
      - role
      - domain
      - stars
      - last_commit
    sort:
      - property: last_commit
        direction: DESC
```

## Review Base

A standalone Base at the vault root, `Reviews.base`, over `type == "review"`, leading with an **Unread** view (`read != true`) so the default view on open is exactly "reports I have not read yet", followed by an **All** view. Both sort by the `date` property descending — newest first — and carry the `read` checkbox as the first column so the report can be ticked off inline. The reports are named by date, so the file name itself is the readable link column (no `title_link` formula needed) and `date` is the sort key only, not a second column.

```yaml
filters:
  and:
    - type == "review"
views:
  - type: table
    name: Unread
    filters:
      and:
        - read != true
    order:
      - read
      - file.name
    sort:
      - property: date
        direction: DESC
  - type: table
    name: All
    order:
      - read
      - file.name
    sort:
      - property: date
        direction: DESC
```
