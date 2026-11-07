# Repairing a note under an older name

How `kboat-note migrate-slugs` moves a note whose filename is not the slug its `url` names; the naming rule it repairs toward is [Naming](../SKILL.md#naming).

A note already in the vault under an older name is repaired by `kboat-note migrate-slugs --dry-run|--apply`, which reports every URL-named note whose filename is not the slug its `url` names and, on `--apply`, renames it.
A PDF source is a pair: `PDFs/<slug>.pdf` and the note's `reading_link` link to it are both derived from the slug, so the file and the link move with the note or nothing does.
Only the filename inside that link is rewritten — a PDF++ page or highlight subpath is where the reader had got to, and it is carried across.
Several things make a row a conflict, all reported and skipped and never overwritten, and the row's `detail` names which.
They group by who clears them, which is what a reader triaging a dry run needs, and the groups are the contract rather than their number.

- **A human merges the two**: an existing note or PDF at a target name, or a slug two notes both want.
- **A human repairs the note**: a `reading_link` that names the note's PDF in a shape the tool cannot rewrite, which would dangle if the pair moved.
- **The vault is what needs looking at**: a target name it refuses to let the pass read at all, a source's PDF it cannot look for because `PDFs/` is absent, not a directory, or refused, or a name held by something that is not a note — a dangling symlink being the one that occurs, which nothing frees on its own.

A note the pass could not read at all is not a row but a `skipped` entry, and a note directory it could not read — absent, not a directory, or refused, the entry saying which — is one too, reported under the directory's own name, because "nothing to migrate" and "nothing I could see" are the same JSON otherwise, and this report is what an `--apply` is approved from.
The counts keep the two apart: `unreadable_dirs` counts the directories and `skipped` counts only the notes, so `counts.skipped` is deliberately smaller than the `skipped` array when a directory is in it.
One directory entry stands for however many notes went unseen, which is why folding it into a note count would put a fixed number where the true one is unknown.
A non-zero exit says a pass left work behind or could not read a directory it had to ([Vault preconditions](../SKILL.md#vault-preconditions)), not that a human must move a file: where the pass renamed the note that was in the way, a re-run clears the conflict on its own.
What no pass can clear is two notes each holding the name the other wants — that one is a human's to break.
A missing vault root is refused in both modes rather than reported as a vault with nothing to migrate, since the dry run is what an `--apply` is approved from.
The tool takes the vault lock for `--apply` and none for `--dry-run`, and it is a repair a human runs, not a routine phase.
