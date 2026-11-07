# Durability and the vault lock in full

The atomic write, the vault lock, how a writer takes it, and what the two leave uncovered.
Who the writers are, and the vault-root precondition both mechanics share, are in [Durability and the vault lock](../SKILL.md#durability-and-the-vault-lock).

## Atomic writes

**Every write the tools make is durable and atomic.**
Content goes to a sibling temp file in the same directory, is flushed and `fsync`ed, is renamed into place with `os.replace`, and the parent directory is `fsync`ed after the rename.
The rename is one atomic syscall, so a crash mid-write never leaves a truncated note behind and the iCloud daemon never picks up a half-written one; the two `fsync`s carry that past a process crash to a power loss, which would otherwise find the rename recorded with the content still in the page cache.
Every write the library makes goes through `kboat.io_utils.atomic_write_text` — the schema-driven note writer, the in-place frontmatter rewrites, and feed-filter's own config write alike.
A raise from it always means nothing was written, which is what lets every caller report a failure or leave an entry for the next run; the directory `fsync` runs after the rename has landed, so its own failure is swallowed rather than allowed to contradict that.
An existing file's permissions survive a rewrite; a file the writer *creates* gets the temp file's own mode (`0o600`), since choosing one would mean overriding the caller's umask.

## The vault lock

**Every mutating run of the tools holds the vault lock.**
Durability alone does not stop two runs from interleaving: each write lands whole, but a run that read a note before another run rewrote it silently overwrites that rewrite when it writes its own version back.
So a process about to mutate the vault takes `kboat.lock.vault_lock(vault)` — an advisory `flock` on a lock file whose contents are a JSON `{pid, started}` record each holder rewrites so a refusal can name who has the vault — and releases it on the way out, including on an exception.
The record is a diagnostic and nothing else; no decision is made from it.

**The lock file lives outside the vault**, in `~/.k-boat/locks/` on every platform, or in `$KBOAT_LOCK_DIR` where that is set.
All contention is same-host, so nothing is gained by syncing it, and inside the iCloud tree the file provider could evict it and bring it back as a different inode, which is the one thing exclusion cannot survive (below).
The file is named by a fixed-length hash of the vault's resolved real path, as the filesystem stores it, so every spelling of one vault — through a symlink, with a trailing slash, in another letter case or Unicode form where the volume ignores those — reaches one lock, and two vaults never share one.
The first acquisition creates the directory, owner-only.
A directory standing in for it through `KBOAT_LOCK_DIR` has to be as local and as permanent: not a synced folder, and not a cache the OS may clear.
A refusal's `holder.path` names the file, which is how a person whose write was refused finds it.
The lock is vault-wide rather than per-note so no writer has to know which folders another touches; one scoped to the folders each writer uses today would have to be re-derived every time a note type moved.
Each tool holds it across its own read as well as its own writes, because a plan computed before another run's writes would act on notes that no longer call for it.

## No stale lock

**There is no stale lock to recover, because the kernel releases it.**
An `flock` belongs to the open file description, so it goes when the file descriptor closes — including on a `SIGKILL`, an OOM kill or a panic.
An interrupted run therefore leaves no lock behind at all: nothing here needs a stale window, an age heuristic, a liveness check on a recorded pid, or a takeover.
**Do not delete the lock file.**
It persists between holds — carrying the last holder's record, which is why it looks like leftover state and is not — because exclusion is an agreement about one inode: remove it mid-run and the next writer creates a different inode and locks that instead, leaving two runs writing at once.
Nothing ever needs it cleared, which is the difference from a design with a stale window a human has to break.
What buys that is a premise worth stating, because it is the one thing that would invalidate the design: **all contention is same-host, on a local lock directory.**
There `flock` is ordinary local advisory locking, including that a holder killed without releasing leaves the lock free.
A lock directory on a network filesystem would need that re-checked, since `flock` over NFS or SMB is where the semantics stop holding, and writers on two hosts would not share a lock at all.

## How a writer takes it

- **A read-only command takes no lock**, so a query neither blocks nor is blocked — `kboat-lifecycle --dry-run`, `kboat-repos refresh --dry-run`, `kboat-repos backfill --dry-run`, `kboat-note migrate-slugs --dry-run`, `kboat-note list`, `kboat-notebooks`, `kboat-pick candidates`, `kboat-queue list`, and `kboat-validate` all read a vault another run is writing.
  - **`kboat-doctor` takes none either**, though its writability probe does write.
    - It creates and removes one uniquely-named file of its own and touches no note, and it runs to find out whether the vault can be written at all.
    - Holding the lock first would be circular, and a pre-flight check that refused whenever a run was in progress would be useless exactly when it is wanted.
- **One policy: wait a little, then refuse.** Every writer takes the lock on the same terms — a few seconds' wait, then a refusal.
  - There is no attended/unattended split, because the `kboat-*` CLIs are run both ways: by a person at a keyboard and by the scheduled routine.
  - A person does not perceive the wait, and an expired one hands back the same holder record an immediate refusal would have.
  - What rules out refusing immediately is that holds are not all short.
    - `kboat-repos refresh` keeps the lock across the `gh` fetch of every note in the catalogue, because its read, fetch and rewrite are one read-modify-write — so "just retry" is not advice a refused caller can act on, and a few seconds of patience covers the ordinary case of overlapping a single note write.
- **A refused caller reports and ends that step.**
  - It prints `{"status": "locked", "holder": {pid, started, path}}` on stdout **in place of** its usual output, names the holding process on stderr, and exits non-zero without writing anything.
  - Every `holder` key is always present and any may be null: the record is written just after the lock is taken, so a refusal landing in that window reads the previous holder's, and a crash mid-write leaves one that does not parse at all.
  - A caller reads that record instead of the keys it came for, so it reports the refusal and ends that step rather than parsing on — that step, not a routine around it, whose later phases make their own attempts.
  - A refusal belongs in the run summary rather than a notification, since the next run recovers on its own.
  - For feed-filter the entry is simply not written, and its never-lost contract carries it: nothing is recorded seen, so the next gather rediscovers it.
    - That is why the wait matters more to it than to a K-Boat phase, whose work survives being deferred — the dispositions, the cooldown clock and the queue are all still on disk and every phase is idempotent.
- **A lock that cannot be taken at all is not a refusal.**
  - A vault root that is missing or cannot be looked up, a lock directory that cannot be created or used, or a filesystem that will not take an `flock` is reported on stderr with **no** `locked` record and an **empty stdout**, because there is no holder and nothing to come back for.
  - The report-shaped CLIs (`kboat-lifecycle`, `kboat-pick set`, `kboat-queue remove`, `kboat-repos refresh`, `kboat-repos backfill --apply`, `kboat-note migrate-slugs --apply`) name it `vault lock unavailable: …`; the note writers fold it into their `write failed: …`, and feed-filter into its `error: …`.
    - What is common to all of them is the shape, not the wording.
  - Do not parse stdout, and do not retry: unlike a refusal this does not clear itself, so report it as needing a human and stop.
  - `kboat-doctor` diagnoses one of its causes and not the rest: its `vault_root` check covers a missing root, but nothing there inspects the lock directory, so one that cannot be created, a lock file that is a directory or *any* symlink (the open is `O_NOFOLLOW`, so a live one fails as surely as a dangling one), or one on a filesystem refusing `flock` leaves doctor reporting `ok` while every write fails.
    - Read the stderr line rather than assuming a green doctor means the lock is fine.
  - How an unattended run raises it belongs to the scheduled-task prompt, not here.

## What is outside

The unit these mechanics protect is one tool invocation writing one file's contents, and five things sit outside it.

- **A file an agent writes itself**, rather than through a `kboat` tool: the distillation review report in `Reviews/` and a rescued source's PDF in `PDFs/`.
  - No tool holds the lock on their behalf, and in the routine each belongs to one phase of one run.
- **A change that spans two files.** Each write is atomic on its own; a pair of them is not.
  - `kboat-repos refresh` adopting a rename writes the new note and then unlinks the old one, so a crash in between leaves both — reported by the next run's `kboat-validate` as two notes for one repo, for a human to merge.
  - `kboat-note migrate-slugs --apply` moves a source's PDF and then the note, ordered so that a crash between them leaves the note where the next scan looks for it, which is what lets a re-run finish the pair rather than lose track of the half that moved.
- **Anything attached to a note's inode rather than its contents.** The atomic write renames a new file over the old one, so a rewrite preserves the note's permissions and nothing else: extended attributes, ACLs, a per-file `com.apple.macl` grant and the creation date all belong to the replaced inode, and a Finder tag on a note the routine rewrites daily will not last.
  - A stray `<name>.md.<random>.tmp` is the same story from the other side — the cleanup runs on an exception, not on a `SIGKILL`, and no scanner globs it because they all read `*.md`.
- **A note saved in Obsidian while a run is writing.** Obsidian holds no lock and writes its own way, and `kboat-lifecycle` and `kboat-pick set` rewrite a whole note, so a hand-edit saved in the same moment loses one side or the other.
  - The remedy is not a mechanism: do not hand-edit notes during a run, and the daily routine runs while nobody is at the keyboard.
- **The judgement an agent forms between its own read and the write it then asks for.** An agent reads a source note, decides a `summary` or a disposition from it, and calls `kboat-note write` some time later; the writer re-reads the note under the lock, so the body, the human's `reading` checkbox, and every field the record omits survive — but a field the record *does* carry overwrites whatever changed in that gap.
  - It is the one lost update the lock does not close, narrowed to the fields one write names.

Closing the agent-level ones would mean exposing acquire and release as a tool of their own, for an agent to hold around a whole phase.
