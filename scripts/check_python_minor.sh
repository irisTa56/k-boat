#!/usr/bin/env bash
# Fail when a newer stable CPython minor exists than the one this workspace
# runs, so a new interpreter release announces itself instead of staying
# unnoticed until someone happens to check by hand.
#
# Does not parse any `pyproject.toml`: uv's own resolution is the source for
# both sides being compared.
#   - "latest": the newest stable CPython uv installs when run outside any
#     project -- an empty directory, so no `requires-python` bounds it. Fetched
#     into a fresh, throwaway UV_PYTHON_INSTALL_DIR made just for this run: a
#     bare `uv python install` (no version pinned) is a no-op -- and reports
#     whatever is already there, however old -- once *any* Python already
#     satisfies it, so reusing an ambient install dir that already holds an
#     interpreter would silently stop this side from ever seeing a new release.
#   - "project": the interpreter uv selects inside this repository, which
#     `requires-python` (owned by packages/kboat/pyproject.toml) does bound.
# uv excludes pre-releases from "latest" by policy -- a pre-release is only
# chosen when no stable release satisfies the request -- so a release
# candidate such as 3.15.0rc2 never trips this check early. See
# https://docs.astral.sh/uv/concepts/python-versions/#pre-release-python-versions
#
# Run from the repository root, as the other scripts/ tools are.

set -euo pipefail

# Never fall back to a `python` already on PATH: both sides must be
# uv-managed CPython builds, or "latest" vs. "project" would compare two
# different kinds of interpreter.
export UV_PYTHON_PREFERENCE=only-managed

# Never fall back to an already-active venv either: `uv run --no-project`
# still prefers one over resolving a fresh interpreter, `--no-project` and
# UV_PYTHON_PREFERENCE notwithstanding. Run this from a shell that has already
# activated this repository's own `.venv` (as `mise activate`/`eval "$(mise
# env)"` do, per the root CLAUDE.md) and, unset, the "latest" side would
# silently report this project's own interpreter back -- always equal to
# "project", so the check would never fire.
unset VIRTUAL_ENV

empty_dir=$(mktemp -d)
latest_install_dir=$(mktemp -d)
venv_dir=$(mktemp -d)
trap 'rm -rf "$empty_dir" "$latest_install_dir" "$venv_dir"' EXIT

latest=$(
  cd "$empty_dir" &&
    UV_PYTHON_INSTALL_DIR="$latest_install_dir" uv python install --no-bin -q &&
    UV_PYTHON_INSTALL_DIR="$latest_install_dir" uv run --no-project python -c 'import sys; print(sys.version_info[1])'
)

# A path outside `.venv`, so this never recreates or disturbs the checkout's
# own environment -- `uv venv` still reads *this* repository's
# `requires-python` to pick the interpreter, it just writes the venv
# somewhere disposable instead of to the default path.
uv venv -q "$venv_dir/venv"
project=$("$venv_dir/venv/bin/python" -c 'import sys; print(sys.version_info[1])')

if [ "$latest" -gt "$project" ]; then
  echo "::error::CPython 3.$latest is out; this workspace's requires-python still tops out at 3.$project -- see the root CLAUDE.md's \"Tooling config\" section for the interpreter-bump procedure." >&2
  exit 1
fi
