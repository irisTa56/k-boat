---
description: One owner per fact — when a copy is allowed, and what a value set costs
paths:
  - "**/*.md"
  - "**/*.py"
  - "**/*.toml"
  - ".github/**/*.yml"
---

# One owner per fact

These rules govern everything in this repository that can state a fact twice: its prose, its toolchain and workflow config, and the code that composes the records its console scripts emit.
A `CLAUDE.md`, a skill, a README, an architecture note, a report's key set where the report is built, and a CI job restating a task the task runner already defines are all inside that.
The daily routine reads those files unattended on every run, so a copy that has stopped matching is acted on before anyone sees it.

## Do not reproduce what another file maintains

Carry a pointer and whatever you add, and name the owner where the pointer sits rather than expecting the reader to find it in an index.
Treat a copy you find as a defect, not as something to keep in sync.

A copy stands only where its reader has to act on the values and will not be opening the owner — a step that executes a precondition, a procedure that branches, a table the schema is checked against, a `description` the harness matches, an invariant put where a local edit would trip over it.
Carry only what that reader acts on, leave the reason to the owner, and take a sweep obligation in place of the ban.
When the fact itself changes, the owner is edited first and the copy after it, never the other way round: a copy edited alone leaves the runtime reader executing the old rule.

Some content is neither a pointer nor a copy: a convention spanning both members, the reason an alternative was rejected, a procedure another file points here to find.
Those originate where they sit because nothing else has a reader for them, and moving one into a member or a skill puts it where half its audience will not look.

## The value-set sweep

Adding, dropping, or renaming a value in a set this project commits to — whether a tool emits it or the project declares it for itself — means reconciling, in the same change, every site where the set is named back: one that branches on it, enumerates it, names a single value of it, or counts it.
Sweep each such file whole rather than the places you remember.

Changing the response a value is owed does the same, and so does writing or editing a list that enumerates the set.
Those two are the triggers that fire where no value changed — the second because a list authored against a stale idea of the set is born incomplete.

For an emitted set the authority is every value the command can produce, wherever along its path the record is composed, and not the enum they start from.
A value a site does not name is not skipped there: it inherits whatever that site does with the values it does name.

The keys a console script prints on stdout are one such set.
Take as few keys, and as few values in any closed set among them, as the prose reading the record actually needs — each one is another site the next sweep has to reach.

Where both sides are structured — a table on one, a field list or an enum on the other — a test can pin that much against drift, which is what `packages/kboat/tests/test_doc_schema_sync.py` does and the pattern to reach for.
What a value obliges its reader to do is structured on neither side, so that stays the sweep's whatever a test covers.
