#!/usr/bin/env bash
# git `post-merge` hook, registered by `mise.toml`'s postinstall: after a merge
# (a fast-forward `git pull` included) that touched what the environment is
# built from, rerun `mise install`, whose postinstall does both `uv sync`s.
# Without it, the scheduled routine keeps running the old environment until
# someone syncs by hand -- or fails on a console script the merge added.
#
# A pyproject change alone is enough reason: `uv.lock` does not record console
# scripts, so a merge that only adds one leaves the lock untouched.
#
# git appends the squash flag (`0` or `1`) as an argument; nothing here reads it.

set -euo pipefail

# The routine runs from the main checkout, and the hook config is shared with
# every linked worktree, where a merge from the base branch must not sync.
if [ "$(git rev-parse --git-dir)" != "$(git rev-parse --git-common-dir)" ]; then
  exit 0
fi

# A git pathspec's `*` crosses `/`, so `*pyproject.toml` reaches every member.
if git diff --quiet ORIG_HEAD HEAD -- mise.toml uv.lock '*pyproject.toml' tools/notebooklm/uv.lock; then
  exit 0
fi

mise install
