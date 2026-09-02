---
name: kboat-curate
description: On-demand maintenance of the K-Boat knowledge base (the `k-boat-knowledge` Basic Memory project) — curate the concept graph and check the concept-note tags for drift and gaps. Use when the user wants to tidy or organize the knowledge base, says things like "curate the KB", "tidy the knowledge base", "check the tags", "are the tags consistent", "find orphans / duplicates", or otherwise asks for a knowledge-graph health pass. Read-mostly; it writes only on confirmation. Defers to memory-curate for the generic graph mechanics, to the KB's `meta/Tag vocabulary` note for the canonical tags, and to kboat-notes for the concept-note conventions.
---

# K-Boat curate

The on-demand maintenance pass for the **knowledge base** — the distilled concept notes in the Basic Memory project `k-boat-knowledge`, rooted at `KBOAT_KNOWLEDGE_PATH`.
It does two things: curate the concept **graph** (orphans, duplicates, naming, relations, sparse notes) and check the concept-note **tags** for drift and gaps.

This skill is run by a human when they want to tidy the KB; the unattended `kboat-routine` does not run it.
It is the agreed home for tag-drift **detection**: the write-time guard in kboat-distill (reuse-first against the vocabulary) prevents most drift, and this pass is the backstop that sweeps what slips through, so the routine carries no separate tag check.

## Scope and boundary

- **Target.** The `k-boat-knowledge` project only. Every Basic Memory call passes `project="k-boat-knowledge"`; the concept notes are at `$KBOAT_KNOWLEDGE_PATH/concepts/*.md`.
- **Not the vault.** Vault-note schema is `kboat-validate`'s job (local, report-only, run by the routine). This skill never touches the vault.
- **Writes only on confirmation.** Audit and report first; apply renames, merges, relation fixes, and tag edits only after the user agrees. Merging concept notes is destructive — propose, never auto-merge (see kboat-distill "Never auto-merge").

## Setup

Load the env so `$KBOAT_KNOWLEDGE_PATH` is set from `.env`:

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
- **Sparse notes** — thin bodies missing Observations or Relations; propose enrichment.

**Renaming a concept note** (Basic Memory resolves wikilinks by title, so a rename breaks inbound `[[...]]`):

1. `search_notes` for the old title to find every note that links it.
2. `edit_note` the title in frontmatter (`find_replace` on the `title:` line) and update each referencing `[[old title]]` → `[[new title]]`.
3. `move_note` (identifier = the permalink) to rename the file; keep the `permalink` unchanged so it stays stable.

## Part B — tag hygiene

The canonical tag set and the variant→canonical aliases live in the KB as the **`meta/Tag vocabulary`** note (`memory://k-boat-knowledge/meta/tag-vocabulary`) — the tag source of truth. Read it first.

1. **Census.** Aggregate every concept note's frontmatter tags:

   ```bash
   awk '/^tags:[[:space:]]*$/{f=1;next} f&&/^- /{t=$0;sub(/^- /,"",t);print t;next} f{f=0}' \
     "$KBOAT_KNOWLEDGE_PATH"/concepts/*.md | sort | uniq -c | sort -rn
   ```

   This assumes the block-style `tags:` form every concept note uses; a note written with an inline array (`tags: [a, b]`) would not be counted, so a surprisingly low total is the cue to check for that form.

2. **Drift.** Compare the census against the vocabulary note:
   - A tag listed under the vocabulary's **Avoid** column → fold it to its canonical form. When the canonical is already on the same note, just drop the variant; otherwise replace it.
   - A tag **not** in the canonical set and not a known alias → a candidate. Judge by the note's content: a typo or near-duplicate of an existing tag is folded (and added to the Aliases table in `meta/Tag vocabulary`); a genuinely new facet is **adopted** — add it to the vocabulary note under the right family in the same change.
   - Leave the "Distinct by design" tags alone (e.g. the three `distributed-*`; `latency`/`throughput` vs `performance`).

3. **Coverage.** List the concept notes with no `tags:` block:

   ```bash
   for f in "$KBOAT_KNOWLEDGE_PATH"/concepts/*.md; do grep -q '^tags:' "$f" || echo "$f"; done
   ```

   For each, propose tags from the canonical set, reuse-first (prefer existing spellings; per-family guidance in the vocabulary note). Insert the `tags:` block as the last frontmatter key (after `permalink:`), matching how the other concept notes carry tags; keep the YAML list indentation identical so the file Basic Memory re-ingests stays valid.

4. **Apply on confirmation.** Edit tag blocks (on disk or via `edit_note`). Keep the two in sync: when you **adopt** a new tag, add it to `meta/Tag vocabulary`; when you **fold** a variant, record it in that note's Aliases table so it does not return.

## Report

Lead with the counts (notes, orphans, duplicate clusters, distinct tags, drift hits, untagged notes), then the proposed actions grouped as graph vs tags, each with the reason.
Apply only what the user confirms; relay what changed.

## Defers to

- **memory-curate** — the generic graph mechanics (orphans, relations, dedup, hub notes).
- **`meta/Tag vocabulary`** (in the KB) — the canonical tags and aliases.
- **kboat-notes** — the concept-note conventions ([Concept notes](../kboat-notes/SKILL.md#concept-notes-kboat_knowledge_path)); **kboat-distill** — the accretion and write-time tag policy.
