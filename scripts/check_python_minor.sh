#!/usr/bin/env bash
# Fail when a newer stable CPython minor is out than the one this workspace
# runs. Run from the repository root.
#
# Both sides come from uv's own resolution rather than from parsing any
# `pyproject.toml`: "latest" is the newest stable CPython the running uv can
# install outside any project, and "project" is the interpreter uv selects
# here under `requires-python`. uv never picks a pre-release while a stable
# release satisfies the request, so a release candidate does not trip this.

set -euo pipefail

# Both sides must be uv-managed builds, not whatever `python` is on PATH.
export UV_PYTHON_PREFERENCE=only-managed

# `uv run --no-project` still prefers an active venv, so with this repository's
# `.venv` active "latest" would report the project's own minor back.
unset VIRTUAL_ENV

empty_dir=$(mktemp -d)
latest_install_dir=$(mktemp -d)
venv_dir=$(mktemp -d)
trap 'rm -rf "$empty_dir" "$latest_install_dir" "$venv_dir"' EXIT

# A fresh install dir: a bare `uv python install` does nothing once any
# interpreter is installed, so a reused dir keeps reporting that one.
latest=$(
  cd "$empty_dir" &&
    UV_PYTHON_INSTALL_DIR="$latest_install_dir" uv python install --no-bin -q &&
    UV_PYTHON_INSTALL_DIR="$latest_install_dir" uv run --no-project python -c 'import sys; print(sys.version_info[1])'
)

# Outside `.venv`, so the checkout's own environment is left alone; `uv venv`
# still picks the interpreter from this workspace's `requires-python`.
uv venv -q "$venv_dir/venv"
project=$("$venv_dir/venv/bin/python" -c 'import sys; print(sys.version_info[1])')

if [ "$latest" -gt "$project" ]; then
  echo "::error::CPython 3.$latest is out; this workspace's requires-python still tops out at 3.$project -- see the root CLAUDE.md's \"Tooling config\" section for the interpreter-bump procedure." >&2
  exit 1
fi
