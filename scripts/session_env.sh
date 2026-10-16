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
# session with no environment at all.
cwd=$(mise -C "$project_dir" exec -- jq -r '.cwd // empty') || cwd=
# The repository root rather than `cwd` itself: a `uv sync` run inside a
# member's directory syncs that member alone and removes the root's dev group.
root=$(git -C "${cwd:-$project_dir}" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$project_dir")

out=$(mise env -s zsh -C "$root")
printf '%s\n' "$out" >"$CLAUDE_ENV_FILE"

# The two venvs the environment above puts on PATH, synced as `mise.toml`'s
# postinstall syncs them: a fresh worktree has neither, and an already-synced
# one is a no-op in well under a second.
mise -C "$root" exec -- uv sync --no-active --quiet --directory "$root"
mise -C "$root" exec -- uv sync --no-active --quiet --directory "$root" --project tools/notebooklm
