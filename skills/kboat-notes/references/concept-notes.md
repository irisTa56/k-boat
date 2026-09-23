# Concept notes (`KBOAT_KNOWLEDGE_PATH`)

Distillation writes concept notes into the Basic Memory project `k-boat-knowledge`, rooted at `KBOAT_KNOWLEDGE_PATH`.
The generic note mechanics and the accretion procedure are defined by the Basic Memory skills — kboat-distill defers to `memory-notes` (note structure), `memory-ingest` (entity matching), and `memory-curate` (merging), the same way kboat-ingest defers to this skill.
What `## Observations` looks like once more than one reading has fed a note is K-Boat's own, and this skill owns it: [Reading groups](#reading-groups) below.

Relations between concepts use wikilinks (`- relation_type [[Other Concept]]`); both ends live in this same root, so they resolve in Basic Memory, Obsidian, and Foam.
That holds only while a note's title is also its filename, because Basic Memory resolves `[[...]]` by title while Obsidian and Foam resolve it by filename.
Basic Memory derives the filename from the title with [`sanitize_for_filename`](https://github.com/basicmachines-co/basic-memory/blob/main/src/basic_memory/file_utils.py), which replaces each of `/ \ < > : " | ? *` with `-`, collapses a run of `-` into one, and strips `.` and `-` from both ends.
So a concept title carries none of those characters and no `--`, and neither starts nor ends with `.` or `-`: write `CPU-GPU` or `A and B`, not `CPU/GPU` or `A / B`.
A title that breaks this still resolves in Basic Memory, so nothing there reports the links it breaks.
Nor does a concept title carry `#`, `^`, `[`, or `]`, which Basic Memory leaves in the filename: in a wikilink, [Obsidian](https://obsidian.md/help/links) and [Foam](https://github.com/foambubble/foam/blob/main/docs/user/features/wikilinks.md) read `#` as the start of a heading link and `#^` as the start of a block link, and a bracket ends the link, so `[[C#]]` points at a heading in a note `C`.
Provenance back to a source is different: the source note lives in the vault, a separate root, so a wikilink to it could not resolve.
Record provenance instead as an observation carrying the source note's `url` as the note holds it, e.g. `- [source] <title> — <url>`.
This is root-independent, stable, and greppable.
Tag each distilled observation by grounding — `#grounded` for claims the source supports, `#dialogue` for external knowledge the reading-time conversation surfaced — so a chat-derived claim is never mistaken for a source claim (kboat-distill defines how the two are sorted and verified).
A claim can also reach the base with no source read at all, from an `ask-kboat` answer the reader chose to keep — [Dialogue records](#dialogue-records) below.
A note's frontmatter facet tags (the snake_case categorisation tags, distinct from the per-observation grounding tags above) come from a controlled vocabulary that lives in the knowledge base itself, as the `meta/Tag vocabulary` note (`memory://k-boat-knowledge/meta/tag-vocabulary`), listing the canonical tags and the variant-to-canonical aliases to avoid.
It is data, not skill config — the right tags depend on what the base accumulates — so kboat-distill reads it when tagging: reuse a canonical tag where one fits, and mint a new one only when none does, recording it in that note in the same change.

These notes are plain Markdown and degrade gracefully: the `## Observations` lines (`- [category] content #tag`) and the in-root relation wikilinks read as ordinary bullets and working links in Obsidian or Foam, so the knowledge stays browsable even without the Basic Memory runtime, which is only the search layer.

## Reading groups

A concept note accretes across readings, and its `## Observations` section is where it says which reading contributed which claims.
The section takes one of two shapes, and a write that half-landed leaves a third:

- **flat** — the claims sit directly under `## Observations` with no `###` heading.
  - A note is written this way from its first reading and stays this way while everything in it is one insight.
- **grouped** — the claims are divided into `###` groups.
  - A group opens with a heading naming what was learned — the angle, not the concept's name repeated — and inside it each reading's claims are followed by that reading's own `- [source]` provenance observation, so a group holding two readings still says which of them each claim came from.

A group is a unit of **insight, not of reading**.
So a group carries two readings' provenance wherever both landed on the same point, and a note whose second reading only deepened the first's insight stays flat rather than being split.
One reading can carry more than one provenance line: a later pass over the same source that places claims the note lacked follows them with that source's line again, so no claim ever sits under another reading's provenance.
Groups run oldest insight first, so a new one is appended at the end of the section, immediately above `## Relations`.
A reading that opens a new group on a flat note is what makes that note grouped, and it owes the claims already there a heading of their own in the same append: leaving them bare would make the earliest insight the one thing in the note that nothing names.
That append is two edits, so a failure or a crash between them can leave the note in the third state — a `###` group with claims still bare above the first heading.
It is not a shape the format admits, and nothing self-heals it on its own: the next append to that note is what heads those claims, which is why kboat-distill owes the heading on any append to a note in it and not only on the one that opens a group.

The provenance lines say which reading each claim came from only while every claim arrives with its own reading's line, so a writer with no reading to name writes no claim.
A reader such as `ask-kboat` takes a claim's origin from the first provenance line below it, and a claim added without its own is credited to whichever reading follows it, with nothing in the text to show otherwise.
Two writers name a new reading: kboat-distill, for a source that was read, and kboat-record-dialogue, for a kept `ask-kboat` answer ([Dialogue records](#dialogue-records)).
A merge of two concept notes writes no new claim: it moves each reading's claims, as they stand, together with the provenance line below them, and places that unit in the surviving note by the same judgement a reading gets.
Everything else that edits a concept note, kboat-curate included, adds no claim: it writes relations, tags, and titles, and at most a provenance line kboat-distill's run summary reported as not landed, which names a reading already in the note.

`## Observations` and `## Relations` each appear exactly once, in that order, and the `###` headings within a note are distinct.
They are anchors, not just structure: kboat-distill positions its inserts relative to them, and Basic Memory resolves a section across the whole note and refuses an insert against one it finds twice or not at all.

**The shape record.**
`kboat-concept shape` reads a concept note's text on stdin and prints `{"shape": "flat"|"grouped"}` — the one key kboat-distill branches on.
It reports whether the section carries **any** `###` group, which is not the same as whether every claim in it is under one: a note in the third state above answers `grouped`, so that state is the writer's to see in the text and not the record's to name.
It opens no file and resolves no title, so it answers about exactly the text it was handed.
Text carrying no `## Observations` heading it **refuses** rather than answers: exit 2 — the code for a record the caller has to fix — with an empty stdout and the reason on stderr.
That is not a third shape but the tool declining to report on something that is not a concept note; kboat-distill's rule is that a shape it did not give is never assumed.
A `###` heading or an anchor inside a fenced code block is not one, because it is not one to Basic Memory's own section matcher either.

It answers that and nothing else, and in particular it is not a gate on whether the note can be written to.
`edit_note` resolves its own anchors and hands back a failure it cannot resolve as part of its own result, which kboat-distill reads and records; a note whose anchors a hand edit broke still takes an insert in most shapes, so refusing one here would only lose claims that would have landed.

## Dialogue records

An `ask-kboat` answer can carry general knowledge the base lacked, and the reader may decide to keep it; `kboat-record-dialogue` is the path that writes it.
Such a claim was never read from anything, so nothing about it can be source-grounded, and the note has to say so where it is read.

- **Its provenance line** is the one form that names no source: `- [source] ask-kboat dialogue in Claude Code, from general knowledge, not a read source — YYYY-MM-DD`, dated the day of the conversation.
  - It carries no URL because there is none, and says "not a read source" so that no reader of the note, `ask-kboat` included, takes the claims above it for something a source stated.
- **Every claim it covers is `#dialogue`**, whatever the recorder's confidence in it: `#grounded` says a source supports the claim, and here no source was read.
- **It is a reading** for [Reading groups](#reading-groups): its claims are followed by its own provenance line, and whether they deepen an insight the note carries or open a new one is the same judgement a source's claims get.

A claim a dialogue record holds can later be grounded by a source that is actually read.
That source's distillation writes the claim again, `#grounded`, as its own, under its own provenance — kboat-distill's replay rule for a claim another reading holds only as `#dialogue` — and the dialogue record's claim stays exactly as it is.

- Retagging it `#grounded` would put a source-grounded claim under a line that says no source was read.
- Moving it under the source's provenance line would credit the source with the dialogue's wording, which the source may not share.

Removing the dialogue copy once a grounded one sits in the note is the reader's call, made by hand, and never a distillation pass's.

## Math and formula notation

A symbol or expression woven into a sentence as prose stays unformatted: `the ratio scales as O(n)` needs no markup.
This holds even when the same variable also appears inside a wrapped formula on the same line: only the formula is marked up, and the prose mention of that variable stays bare.
Mark up only an expression presented **as** a formula, equation, or named quantity — an expression on its own, a definition, a derivation — and choose the markup by how the notation is written, not by whether the content is "mathematical":

- If plain ASCII represents it faithfully — arithmetic or pseudocode over `= + − × ÷ /`, parentheses, and named variables (`KV bytes = 2 × num_kv_heads × head_dim × dtype_bytes`) — wrap it in **code**: an inline span for a short expression, a fenced block for a multi-line one.
  - This is lossless, since the ASCII already written is the content; it renders the same everywhere with no MathJax dependency; and it is the default whenever the two cases are close.
- If the notation needs math typography that ASCII degrades — stacked fractions, Σ/∏/∫ with limits, binomial coefficients, sub/superscript stacks, or Greek letters used as variables (`(1/k)·log2(C choose k)`, `Δ̂(t) = Q(e(t) + Δ(t))`) — wrap it in **LaTeX**: `$…$` inline, `$$…$$` for a display equation, so Obsidian's MathJax renders it.

The split keeps the write-time decision objective — "does ASCII represent this faithfully?" rather than the harder "is this math?" — and the code default is always safe.
A single note may mix both: a code-wrapped ratio beside a `$$`-rendered sum is normal.
