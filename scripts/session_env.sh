#!/usr/bin/env bash
# SessionStart hook: write the project's mise environment to CLAUDE_ENV_FILE,
# and sync the workspace venv it puts on PATH. Reads the hook's input JSON on
# stdin.
#
# The checkout is taken from the input's `cwd`, not from CLAUDE_PROJECT_DIR:
# in a worktree session the desktop app starts, CLAUDE_PROJECT_DIR and the
# hook's own working directory are the main checkout, and only `cwd` is the
# worktree (https://code.claude.com/docs/en/worktrees). Reading the main
# checkout's environment there would put its `.venv` first on PATH, so tests
# run from the worktree would import the main checkout's code.

set -euo pipefail

if [ -z "${CLAUDE_ENV_FILE:-}" ] || ! command -v mise >/dev/null 2>&1; then
  exit 0
fi

project_dir=${CLAUDE_PROJECT_DIR:-$PWD}
# A failed lookup falls back to CLAUDE_PROJECT_DIR rather than leaving the
# session with no environment at all, and fails the hook once the rest has run:
# in a worktree session that is the main checkout's environment, which Claude
# Code would otherwise load without a word.
status=0
if ! cwd=$(mise -C "$project_dir" exec -- jq -r '.cwd // empty'); then
  printf 'session_env: could not read the hook input; loaded the environment of %s\n' "$project_dir" >&2
  cwd=
  status=1
fi
# The repository root rather than `cwd` itself: a `uv sync` run inside a
# member's directory syncs that member alone and removes the root's dev group.
root=$(git -C "${cwd:-$project_dir}" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$project_dir")
# Only a checkout of this repository: a `cwd` in some other repository would
# load that one's environment and sync its venv.
common_dir() { git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null; }
if [ "$(common_dir "$root")" != "$(common_dir "$project_dir")" ]; then
  root=$project_dir
fi

# A worktree sits under the main checkout (`.claude/worktrees/`), and mise
# reads config files up the tree, so without a ceiling the main checkout's
# `.venv/bin` follows the worktree's on PATH and whatever the worktree's venv
# lacks resolves to the main checkout's code.
export MISE_CEILING_PATHS
MISE_CEILING_PATHS=$(dirname "$root")

out=$(mise env -s zsh -C "$root")
printf '%s\n' "$out" >"$CLAUDE_ENV_FILE"

# The two venvs the environment above puts on PATH, synced as `mise.toml`'s
# postinstall syncs them: a fresh worktree has neither, and an already-synced
# one is a no-op in well under a second.
mise -C "$root" exec -- uv sync --no-active --quiet --directory "$root"
mise -C "$root" exec -- uv sync --no-active --quiet --directory "$root" --project tools/notebooklm
exit "$status"
