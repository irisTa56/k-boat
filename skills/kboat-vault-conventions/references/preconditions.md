# Vault preconditions: the checks and the report

What `kboat-doctor` checks, why each check is drawn where it is, and the report it prints.
What the check is for, and the rule a command reading the same set holds itself to, are in [Vault preconditions](../SKILL.md#vault-preconditions).

## The checks

- **The environments the routine runs match the checkout's locks**, as `environment`, whose `paths` name each one that does not: `kboat` for kboat's runtime closure in the workspace venv, `tools/notebooklm` for the NotebookLM CLI's own venv.
  - The case it is for is a checkout whose code has moved past its venvs — the post-merge hook did not run, or its sync failed — so a phase would fail with `command not found` mid-run, or run on a version the checkout no longer locks.
    - The remedy is `mise install` in that checkout, whose `postinstall` runs both syncs.
  - It asks uv itself (`uv sync --check`), so a console script the venv lacks is found as well as a stale version, and nothing here re-reads `pyproject.toml`.
    - `--inexact --no-dev` keeps out what the routine does not import — the dev tooling, and the other members' dependencies — so a stale one of those does not stop it.
    - `--offline`, so the check every run waits on does not wait on the network.
  - The checkout is the one the running `kboat` sits in, not wherever the caller stands: a bare `kboat-doctor` is found on `PATH`, and its venv is the one the run's other bare calls land on.
  - uv that cannot be run fails the check with the reason in `detail`, like an environment that is out of date, since the answer is then unknown.
  - It comes first and runs whatever the vault's state, since nothing about the vault bears on it.
- **The root exists** and is a directory.
  - Absent, or a name held by something that is not one, short-circuits the vault checks after it: every one of them would only restate the same fact.
  - A root the vault **refuses to read** does not short-circuit, and this is the third answer rather than a shade of the first.
    - Writability and readability each have their own finding about such a root, and a run that stopped would report none of them — telling a human that a vault which is there and merely unreadable is a vault that is gone.
- **The root is writable** — the root itself, not each folder the run writes into.
  - Proven by creating a uniquely-named probe file with `O_EXCL` and unlinking it: permission bits are not the whole story on a synced volume, so the check writes rather than inspects.
  - A probe that cannot be removed again fails too, since a file left in the vault is itself drift.
  - A single folder made read-only under a writable root therefore passes here and fails mid-run; the check is aimed at a vault that is absent or not mounted, which is the failure that takes the root with it.
- **The required folders exist**, and separately that **no required name is taken by a non-directory** (a file, or a dangling symlink).
  - Two checks, because the first wants `mkdir` and the second wants the name freed first — `mkdir` on a taken name fails.
  - The set is every note type's directory (from `kboat.schema`'s `DIR_BY_TYPE`, so declaring a new type requires its folder) plus `Queue/`, `Reviews/`, and `PDFs/` (named beside `DIR_BY_TYPE` in `kboat.schema`, so the layout has one home).
  - The writer would create a missing one on first write, but the check does not defer to that: an absent folder is indistinguishable from a vault that has not synced, and auto-creating it is how a run comes to write into a half-synced vault.
- **The questions file exists** — `Questions.md` at the vault root, the daily pick's backlog.
- **Every scanned directory can be read**, as `readable_notes` and `readable_assets`.
  - No scan can stand in for this.
    - A directory the OS refuses to list is what a scan reads as an empty one unless it was written not to: `Path.glob` swallows the refusal, and `is_dir()` still answers `True` because that `stat` goes through the parent.
  - Within the scanned set, it is what stops the run before each phase's own scan reports the folder separately.
  - Outside that set only the phase's own scan reports one.
    - `Daily/` is the case — read by the daily pick and deliberately no precondition of it — so `kboat-pick candidates` reports an unreadable `Daily/` itself, since this check never will.
  - The pair splits by what a refusal actually costs rather than by which directory it is in, because a doctor failure stops the whole routine.
    - A note directory **fails** however it is unreadable: its listing is a phase's input.
    - An asset directory that is unlistable but still **traversable** only warns.
      - No phase lists `PDFs/` — ingest writes one path and the slug migration probes one by name — so nothing is affected, and a failure would stop the routine over it.
    - The **asset directory itself** failing to be traversable is the exception, because there every access by name fails: ingest cannot write a source's PDF, and the slug migration cannot tell where one is, so it moves no source at all.
    - A directory *below* it warns whatever its mode: the probes name `PDFs/<slug>.pdf` at the top level, so nothing there is affected, and failing would stop the whole routine over a folder no phase reads.
  - The scan is recursive and the failure deliberately wider than a phase's input: a subfolder under a note directory is one nothing lists, and it fails all the same, because what the check establishes is that the vault can be read rather than that today's phases happened to reach everything in it.
  - A directory that goes away mid-scan is not a refusal and only **warns**: the walk listed a parent and the child was gone by the time it descended, which clears itself before anyone can act.
    - The refusals name their `strerror` in `detail`, so the two are told apart without parsing a name out of `paths`.
- **No file has been evicted** in the scanned set — the note directories and `PDFs/`, each recursively — reported as `evicted_files`, a **warning** that never fails.
  - iCloud evicts a file in place: the name stays and only the content leaves, marked by the `SF_DATALESS` flag, and the first read downloads it again.
    - So an evicted file is listed, probed, and read like any other, and a run that meets one pays a download rather than missing an input; failing over it would stop a run that would have succeeded.
    - What a read does when it cannot download — fail, or wait — has not been measured; the routine needs the network for NotebookLM in any case.
    - Before macOS Sonoma iCloud left a `.<name>.icloud` placeholder instead and the name itself went away; no host K-Boat runs on does that, so nothing here looks for one.
  - It warns at all because the vault folder is meant to be kept downloaded.
    - A file another device has just added is dataless too until this Mac downloads it, even in a folder kept downloaded, so one report can be a sync still in progress; only the same file reported again on a later run says that setting has lapsed.
  - Recursively, but not into a symlinked subdirectory, since one symlink loop would hang the check every run waits on.
  - The sweep is only as complete as `readable_notes` and `readable_assets`: a directory that could not be listed holds no findings for this check either, so an `evicted_files` of `ok` beside a failing readability check says nothing was found rather than that nothing is there.

## The report

The report on stdout is a JSON object with `vault`, `ok` (true when nothing failed), `checks`, and `counts`.
Each entry in `checks` carries `name`, `status` (`ok`, `warning`, or `failed`), `detail`, `paths`, and `path_count` — every key on every entry, and `counts` likewise carries `total` plus one count per status even at zero, so a reader never has to decide whether an absent key means empty or means nothing.
A check's names live in its `paths` alone, never restated in `detail`, so a reader acting on one list is not left wondering whether the other holds more.
`paths` names at most five, with `path_count` giving the true total: the failure these checks exist for can evict thousands of files at once, and the caller is an unattended agent whose context both streams land in, so the report is bounded rather than answering a question about the vault with the vault itself.
A **short-circuiting** root failure — absent, or the name held by a non-directory — is reported without the vault checks after it, so that run has fewer entries but never fewer keys.
Every non-`ok` check is also written to stderr, one line per finding, so an unattended log shows the reason without parsing the JSON back.
