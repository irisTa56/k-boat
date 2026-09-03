# Concept notes (`KBOAT_KNOWLEDGE_PATH`)

Distillation writes concept notes into the Basic Memory project `k-boat-knowledge`, rooted at `KBOAT_KNOWLEDGE_PATH`.
The generic note mechanics and the accretion procedure are defined by the Basic Memory skills — kboat-distill defers to `memory-notes` (note structure), `memory-ingest` (entity matching), and `memory-curate` (merging), the same way kboat-ingest defers to this skill.
What `## Observations` looks like once more than one reading has fed a note is K-Boat's own, and this skill owns it: [Reading groups](#reading-groups) below.

Relations between concepts use wikilinks (`- relation_type [[Other Concept]]`); both ends live in this same root, so they resolve in Basic Memory, Obsidian, and Foam.
Provenance back to a source is different: the source note lives in the vault, a separate root, so a wikilink to it could not resolve. Record provenance instead as an observation carrying the source's canonical URL, e.g. `- [source] <title> — <url>`. This is root-independent, stable, and greppable.
Tag each distilled observation by grounding — `#grounded` for claims the source supports, `#dialogue` for external knowledge the reading-time conversation surfaced — so a chat-derived claim is never mistaken for a source claim (kboat-distill defines how the two are sorted and verified).
A note's frontmatter facet tags (the snake_case categorisation tags, distinct from the per-observation grounding tags above) come from a controlled vocabulary that lives in the knowledge base itself, as the `meta/Tag vocabulary` note (`memory://k-boat-knowledge/meta/tag-vocabulary`), listing the canonical tags and the variant-to-canonical aliases to avoid.
It is data, not skill config — the right tags depend on what the base accumulates — so kboat-distill reads it when tagging: reuse a canonical tag where one fits, and mint a new one only when none does, recording it in that note in the same change.

These notes are plain Markdown and degrade gracefully: the `## Observations` lines (`- [category] content #tag`) and the in-root relation wikilinks read as ordinary bullets and working links in Obsidian or Foam, so the knowledge stays browsable even without the Basic Memory runtime, which is only the search layer.

## Reading groups

A concept note accretes across readings, and its `## Observations` section is where it says which reading contributed which claims.
The section takes one of two shapes, and a write that half-landed leaves a third:

- **flat** — the claims sit directly under `## Observations` with no `###` heading. A note is written this way from its first reading and stays this way while everything in it is one insight.
- **grouped** — the claims are divided into `###` groups. A group opens with a heading naming what was learned — the angle, not the concept's name repeated — and inside it each reading's claims are followed by that reading's own `- [source]` provenance observation, so a group holding two readings still says which of them each claim came from.

A group is a unit of **insight, not of reading**.
So a group carries two readings' provenance wherever both landed on the same point, and a note whose second reading only deepened the first's insight stays flat rather than being split.
Groups run oldest insight first, so a new one is appended at the end of the section, immediately above `## Relations`.
A reading that opens a new group on a flat note is what makes that note grouped, and it owes the claims already there a heading of their own in the same append: leaving them bare would make the earliest insight the one thing in the note that nothing names.
That append is two edits, so a failure or a crash between them can leave the note in the third state — a `###` group with claims still bare above the first heading.
It is not a shape the format admits, and nothing self-heals it on its own: the next append to that note is what heads those claims, which is why kboat-distill owes the heading on any append to a note in it and not only on the one that opens a group.

`## Observations` and `## Relations` each appear exactly once, in that order, and the `###` headings within a note are distinct.
They are anchors, not just structure: kboat-distill positions its inserts relative to them, and Basic Memory resolves a section across the whole note and refuses an insert against one it finds twice or not at all.

**The shape record.** `kboat-concept shape` reads a concept note's text on stdin and prints `{"shape": "flat"|"grouped"}` — the one key kboat-distill branches on. It reports whether the section carries **any** `###` group, which is not the same as whether every claim in it is under one: a note in the third state above answers `grouped`, so that state is the writer's to see in the text and not the record's to name.
It opens no file and resolves no title, so it answers about exactly the text it was handed.
Text carrying no `## Observations` heading it **refuses** rather than answers: exit 2 — the code for a record the caller has to fix — with an empty stdout and the reason on stderr.
That is not a third shape but the tool declining to report on something that is not a concept note; kboat-distill's rule is that a shape it did not give is never assumed.
A `###` heading or an anchor inside a fenced code block is not one, because it is not one to Basic Memory's own section matcher either.

It answers that and nothing else, and in particular it is not a gate on whether the note can be written to.
`edit_note` resolves its own anchors and hands back a failure it cannot resolve as part of its own result, which kboat-distill reads and records; a note whose anchors a hand edit broke still takes an insert in most shapes, so refusing one here would only lose claims that would have landed.

## Math and formula notation

A symbol or expression woven into a sentence as prose stays unformatted: `the ratio scales as O(n)` needs no markup.
This holds even when the same variable also appears inside a wrapped formula on the same line: only the formula is marked up, and the prose mention of that variable stays bare.
Mark up only an expression presented **as** a formula, equation, or named quantity — an expression on its own, a definition, a derivation — and choose the markup by how the notation is written, not by whether the content is "mathematical":

- If plain ASCII represents it faithfully — arithmetic or pseudocode over `= + − × ÷ /`, parentheses, and named variables (`KV bytes = 2 × num_kv_heads × head_dim × dtype_bytes`) — wrap it in **code**: an inline span for a short expression, a fenced block for a multi-line one. This is lossless, since the ASCII already written is the content; it renders the same everywhere with no MathJax dependency; and it is the default whenever the two cases are close.
- If the notation needs math typography that ASCII degrades — stacked fractions, Σ/∏/∫ with limits, binomial coefficients, sub/superscript stacks, or Greek letters used as variables (`(1/k)·log2(C choose k)`, `Δ̂(t) = Q(e(t) + Δ(t))`) — wrap it in **LaTeX**: `$…$` inline, `$$…$$` for a display equation, so Obsidian's MathJax renders it.

The split keeps the write-time decision objective — "does ASCII represent this faithfully?" rather than the harder "is this math?" — and the code default is always safe.
A single note may mix both: a code-wrapped ratio beside a `$$`-rendered sum is normal.
