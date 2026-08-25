---
name: kboat-curate
description: On-demand maintenance of the K-Boat knowledge base (the `k-boat-knowledge` Basic Memory project) — curate the concept graph, check the concept notes' shape, and check their tags for drift and gaps. Use when the user wants to tidy or organize the knowledge base, says things like "curate the KB", "tidy the knowledge base", "check the tags", "are the tags consistent", "are my concept notes well-formed", "find orphans / duplicates", or otherwise asks for a knowledge-graph health pass. Read-mostly; it writes only on confirmation. Defers to memory-curate for the generic graph mechanics, to the KB's `meta/Tag vocabulary` note for the canonical tags, and to kboat-notes for the concept-note conventions.
---

# K-Boat curate

The on-demand maintenance pass for the **knowledge base** — the distilled concept notes in the Basic Memory project `k-boat-knowledge`, whose own root Basic Memory holds and which `KBOAT_KNOWLEDGE_PATH` is expected to match.
It does three things: curate the concept **graph** (orphans, duplicates, naming, relations, sparse notes), check the concept notes' **shape** with `kboat-knowledge`, and check their **tags** for drift and gaps.

This skill is run by a human when they want to tidy the KB; the unattended `kboat-routine` does not run it.
It is the agreed home for tag-drift **detection**: the write-time guard in kboat-distill (reuse-first against the vocabulary) prevents most drift, and this pass is the backstop that sweeps what slips through, so the routine carries no separate tag check.

## Scope and boundary

- **Confirm the two roots agree before anything else.** This pass reads from both — Part A and Part B step 4 go through the project, Part A2 and Part B steps 1 and 3 through `$KBOAT_KNOWLEDGE_PATH` — and nothing ties them: `mise.toml`'s documented default already differs from the live project's path, so only `.env` closes the gap. Compare `kboat-knowledge`'s reported `knowledge` against the `k-boat-knowledge` entry's `path` from `list_memory_projects(output_format="json")` — the default `text` form prints no path — and **stop** on a mismatch. Where Basic Memory is down the comparison cannot be made at all: run the two halves that never touch it (Part A2 and Part B steps 1 and 3), report them as unconfirmed against the project root, and defer the graph audit and every write, rather than refusing the pass outright. Otherwise one report mixes a graph audit of one base with a shape scan and a tag census of another, and the writes are confirmed against it.
- **Target.** The `k-boat-knowledge` project only. Every Basic Memory call passes `project="k-boat-knowledge"`; the concept notes are under `$KBOAT_KNOWLEDGE_PATH/concepts/`, at any depth — a subfolder is allowed there, so every scan in this skill walks the tree rather than globbing one level.
- **Not the vault.** Vault-note schema is `kboat-validate`'s job (local, report-only, run by the routine). This skill never touches the vault.
- **Writes only on confirmation.** Audit and report first; apply renames, merges, relation fixes, and tag edits only after the user agrees. Merging concept notes is destructive — propose, never auto-merge (see kboat-distill "Never auto-merge").

## Setup

Load the env so `$KBOAT_KNOWLEDGE_PATH` is set from `.env` and the `kboat-*` scripts resolve bare. Every block below repeats it, because the Bash tool keeps no state between them:

```bash
eval "$(mise env)"
```

Basic Memory must be reachable (it is the search/query layer). If it is down, the tag census still works (it reads files on disk), but the graph audit (`memory-curate`) does not — say so and defer that half.
On-disk frontmatter edits are picked up by Basic Memory's file watcher, so editing a tag block directly is fine; new notes and relation edits go through the Basic Memory tools.

## Part A — graph health

Invoke the **memory-curate** skill for the generic mechanics, scoped to `k-boat-knowledge` — do not hand-roll an equivalent graph audit inline:

- **Orphans** — concept notes with no inbound or outbound relations; propose relations or a hub note.
- **Duplicates / overlaps** — clusters covering the same ground; propose an index note or relations, not a merge (log merge candidates for a human).
- **Naming** — flag vague titles, especially a generic phrase narrowed by a parenthetical qualifier (the pattern `Generic phrase (what it is really about)`, clearer rewritten as `Specific phrase`); propose a clearer title.
- **Relations** — high-confidence missing edges, and contradictions (the same pair related one way from one side and another from the other); reconcile to one direction.
- **Folders** — `memory-curate` proposes grouping a crowded folder into subfolders. That is fine for `concepts/`, which the shape check walks to any depth, but a concept note moved anywhere else leaves the check entirely; it comes back as `note_outside_concepts` below — unless it lands under `meta/`, which the check treats as out of scope, so a proposal to move one there is the one this pass has to judge itself, and until it is moved back nothing reads its shape.
- **Sparse notes** — thin bodies missing Observations or Relations; propose relations, or record the concept as one a future source should feed — in the report you hand the user, not in the note: `## Observations` and `## Relations` have to stay adjacent, so a new section between them would take the next reading group with it. Never hand-write a **claim** into a concept note: every claim there belongs to a reading group and reads as one that group's source made, so one added by hand would state a provenance no source gave it. Restoring a `[source]` line whose source you have identified is not that — it is the repair `unclosed_claims` asks for, and the only way out of a note that would otherwise stay blocked. A hub or index note under `meta/` is not a concept note and carries its `#meta` observations by design.

**Renaming a concept note** (Basic Memory resolves wikilinks by title, so a rename breaks inbound `[[...]]`):

1. `search_notes` for the old title to find every note that links it.
2. `edit_note` the title in frontmatter (`find_replace` on the `title:` line) and update each referencing `[[old title]]` → `[[new title]]`.
3. `move_note` (identifier = the permalink) to rename the file; keep the `permalink` unchanged so it stays stable.

## Part A2 — the concept-note shape

`memory-curate` has no notion of the concept-note section shape or of reading groups, so this is a pass of its own rather than part of Part A's invocation.
It is mechanical and it is the only check there is over the knowledge root, so it lives in the `kboat` library rather than as a scan written out here:

```bash
eval "$(mise env)" && kboat-knowledge
```

Read-only, and it reports `checked` alongside the findings — pass both on, even at zero. The findings count alone cannot tell a clean base from an empty `concepts/`, or from a check that never ran; `checked` is what separates them.
Every finding carries its own `repair` and a `disposition` saying who acts on it. `base` means the report is not about the knowledge base at all, so no count in it describes the whole — fix that and run it again. `blocks` and `passes` are below.

`blocks` means the distillation pass will not write into that note, `passes` that nothing is owed and the next distillation settles it, and `uncovered` that the check has no answer about that file. Where a concept maps to a `blocks` finding, that is an outage in progress — every source needing the concept is being aborted and replayed — and those are the findings to work first. Those are the `blocks` ones. An `uncovered` finding is about a file the check does not cover, and its own `repair` says which kind: a concept note that has left `concepts/` is real work — its shape goes unread while the pass keeps writing into it — while a file that is no part of the base needs none. Nothing is being aborted by either, so neither is the work to do first. Read each finding's `repair` from the report rather than from a list kept here: the list lives with the codes, in the library, where a new one cannot be added without it.

## Part B — tag hygiene

The canonical tag set and the variant→canonical aliases live in the KB as the **`meta/Tag vocabulary`** note (`memory://k-boat-knowledge/meta/tag-vocabulary`) — the tag source of truth. Read it first.

1. **Census.** Aggregate every concept note's frontmatter tags:

   ```bash
   eval "$(mise env)"
   find "$KBOAT_KNOWLEDGE_PATH/concepts" -name '*.md' -print0 | xargs -0 \
     awk '/^tags:[[:space:]]*$/{f=1;next} f&&/^- /{t=$0;sub(/^- /,"",t);print t;next} f{f=0}' \
     | sort | uniq -c | sort -rn
   ```

   This assumes the block-style `tags:` form every concept note uses; a note written with an inline array (`tags: [a, b]`) would not be counted, so a surprisingly low total is the cue to check for that form.

2. **Drift.** Compare the census against the vocabulary note:
   - A tag listed under the vocabulary's **Avoid** column → fold it to its canonical form. When the canonical is already on the same note, just drop the variant; otherwise replace it.
   - A tag **not** in the canonical set and not a known alias → a candidate. Judge by the note's content: a typo or near-duplicate of an existing tag is folded (and added to the Aliases table in `meta/Tag vocabulary`); a genuinely new facet is **adopted** — add it to the vocabulary note under the right family in the same change.
   - Leave the "Distinct by design" tags alone (e.g. the three `distributed-*`; `latency`/`throughput` vs `performance`).

3. **Coverage.** List the concept notes with no `tags:` block:

   ```bash
   eval "$(mise env)"
   find "$KBOAT_KNOWLEDGE_PATH/concepts" -name "*.md" | while read -r f; do grep -q '^tags:' "$f" || echo "$f"; done
   ```

   For each, propose tags from the canonical set, reuse-first (prefer existing spellings; per-family guidance in the vocabulary note). Insert the `tags:` block as the last frontmatter key (after `permalink:`), matching how the other concept notes carry tags; keep the YAML list indentation identical so the file Basic Memory re-ingests stays valid.

4. **Apply on confirmation.** Edit tag blocks (on disk or via `edit_note`). Keep the two in sync: when you **adopt** a new tag, add it to `meta/Tag vocabulary`; when you **fold** a variant, record it in that note's Aliases table so it does not return.

## Report

Lead with the counts (notes, orphans, duplicate clusters, the shape scan's `checked` and findings, distinct tags, drift hits, untagged notes), then the proposed actions grouped as shape, graph and tags, each with the reason. The shape group is one entry per finding — the note and its own `repair` — and it comes first, because Part A2 says to work the `blocks` ones first and this skill applies only what the user confirms: a finding that reaches the report as a number alone is one nothing here can act on, and `kboat-distill` keeps aborting the sources that need that concept. Report the shape scan's two numbers even when the findings are zero, for the reason Part A2 gives.
Apply only what the user confirms; relay what changed.

## Defers to

- **memory-curate** — the generic graph mechanics (orphans, relations, dedup, hub notes).
- **`meta/Tag vocabulary`** (in the KB) — the canonical tags and aliases.
- **kboat-notes** — the concept-note conventions ("Concept notes"); **kboat-distill** — the accretion and write-time tag policy.
