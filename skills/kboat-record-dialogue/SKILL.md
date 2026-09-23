---
name: kboat-record-dialogue
description: Record into the K-Boat knowledge base (the `k-boat-knowledge` Basic Memory project) what a conversation with no source behind it turned up — typically an `ask-kboat` answer carrying general knowledge the base lacked, which the user has decided to keep. Use when the user brings back such a question and answer and says things like "record this", "add this answer to the KB", "save what ask-kboat told me". Interactive: it proposes every write and makes only the ones the user confirms. Defers to kboat-notes for what a dialogue record carries (its provenance line, the `#dialogue` tag, its place in reading groups) and to kboat-distill's accretion policy for where claims go in a concept note.
---

# K-Boat record dialogue

`ask-kboat` answers from the knowledge base and never writes to it; where its answer carried general knowledge the base lacked, it leaves the decision to record that to the user.
This skill is the path once they decide to.
It turns the answer into concept-note writes that follow kboat-notes [Dialogue records](../kboat-notes/references/concept-notes.md#dialogue-records), and makes them only on the user's confirmation.
kboat-distill writes from sources that were read; a conversation with no source reaches the base only through here.

## Scope

- **Target.** The `k-boat-knowledge` project only; every Basic Memory call passes `project="k-boat-knowledge"`.
  - Nothing is written to the vault, so this takes no vault lock and touches no source note.
- **Input.** The question and the answer, as the user hands them over — pasted, or a file they name — and the date of the conversation.
  - This session did not take part in that conversation, so ask for what is missing rather than reconstructing it, the date included where the material does not show it.
- **Not a distillation.** There is no notebook, no source note, no `Reviews/` section, and no stamp.
  - The user approves each write before it is made, so kboat-distill's after-the-fact review gate and its create cap, both there because that pass runs unattended, do not apply.
- **Committing is not this skill's.** The knowledge root is a Git working tree of its own; leave the commit to the user, under that repository's conventions.

## Setup

Run `eval "$(mise env)"` at the top of each shell block, so `kboat-concept` resolves bare (see kboat-notes [Environment](../kboat-notes/SKILL.md#environment)).

Probe Basic Memory once with `search_notes(project="k-boat-knowledge", …)`.
If the call errors, stop and report it: the material is in the user's hands, so waiting for a healthy day loses nothing.

## Procedure

### 1. Take the answer apart

Split the answer into claims, one per observation it would become, and set aside two kinds:

- a claim the answer took from the base itself, which the base already holds — `ask-kboat` cites the notes such claims came from;
- what concerns the conversation rather than the subject, such as how the question was framed or advice addressed to the reader.

Verify each remaining claim with your own knowledge, and take one of kboat-distill's three actions for a dialogue claim (its accretion policy, "Correct dialogue-derived claims"): keep it as stated, correct what is wrong where you can do so confidently, or drop it.
The answer came from a model's general knowledge, and this step is the last point at which anything checks it.
A claim you corrected or dropped goes into the proposal with what changed and why, so the user meets the change there rather than in the note.

Where a claim contradicts one the base holds — the disagreement `ask-kboat` surfaces rather than settles — put both to the user.
If they still want it recorded, it goes in beside the claim it contradicts and never edits or replaces it.

### 2. Decide where each claim goes

Work out each concept's writes under kboat-distill's "Accretion policy", reading it for these rules:

- **Append first** — the search variations, `kboat-concept shape`, where a placement goes, and the heading a flat note owes when these claims open a new insight;
- **Create only specific concepts** — and the title rules it points to;
- **Reuse facet tags from the vocabulary** — for a note this creates;
- **Mark up formula notation**;
- **Stay idempotent on replay** — for its skip rules alone: a claim or relation the note already holds is not written again;
- **Never auto-merge**.

What that policy says about sources is the one part that changes: this conversation is one reading of each note it feeds, its claims all `#dialogue` and followed by its own provenance line, as kboat-notes [Dialogue records](../kboat-notes/references/concept-notes.md#dialogue-records) sets out.

### 3. Propose, then write

Before any write, show the user, note by note:

- whether the note is created or appended to;
- the exact observation lines, with the provenance line after them and any `###` heading the placement owes;
- the relations to be added, and the notes that carry them;
- a facet tag to be minted, with its entry in `meta/Tag vocabulary`;
- the claims step 1 corrected or dropped.

Write only what they confirm.
This is `memory-ingest`'s approval gate applied to these writes, and nothing more of that skill: do not create its verbatim source note, since the provenance line is the dialogue's whole record in the base.

Make the writes the way kboat-distill makes them, `edit_note` with `output_format="json"` read for a non-null `error`, then read each note back.
A `write_note` that timed out may still have created its note, and one that holds these claims was written, whatever the call returned.
Report what landed and what did not, note by note.
