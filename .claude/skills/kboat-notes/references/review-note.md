# Review note (`Reviews/*.md`)

A review report (`Reviews/YYYY-MM-DD.md`, written by kboat-distill — see its "Review report" for the body) is a plain Markdown document, not a schema'd note: it sits outside `kboat.schema`, so `kboat-note` never writes it and `kboat-validate` never checks it.
It carries only a small frontmatter block, written once when the run first creates the file, to drive the read-tracking [Review Base](bases.md#review-base):

```yaml
---
type: review
date: <YYYY-MM-DD>
read: false
---
```

- `type: review` — the Base filter key, parallel to `source`/`kindle`/`repo`. It is also what keeps a report *in* the Base: the Base's top filter is `type == "review"`, so a report written without this block drops out of the Base entirely (not just the `read` column) — which is why the block is mandatory, see kboat-distill "Review report".
- `date` — the run date, the same value as the filename; the Base's sort key (the filename is already a date, so date and filename order are identical, but a typed property is the explicit, robust key).
- `read` — the **human's** read-tracking flag, the always-present boolean here. The routine writes it `false` and never reads or rewrites it; you toggle it (inline in the Base or in the note) once you have read the report. It is always present so the Base can filter `read != true` for an unread view, the same always-present-boolean rule the other Bases follow.
