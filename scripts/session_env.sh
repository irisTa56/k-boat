#!/bin/sh
# SessionStart hook (.claude/settings.json). CLAUDE.md "Environment" says why
# it reads the checkout from its input's `cwd` and stops mise's lookup there.

[ -n "$CLAUDE_ENV_FILE" ] && command -v mise >/dev/null 2>&1 || exit 0

dir=$(jq -r '.cwd // empty')
dir=$(git -C "${dir:-$CLAUDE_PROJECT_DIR}" rev-parse --show-toplevel 2>/dev/null || echo "$CLAUDE_PROJECT_DIR")
MISE_CEILING_PATHS=$(dirname "$dir")
export MISE_CEILING_PATHS

out=$(mise env -s zsh -C "$dir") || exit
printf '%s\nexport MISE_CEILING_PATHS=%s\n' "$out" "$MISE_CEILING_PATHS" >"$CLAUDE_ENV_FILE"

mise -C "$dir" exec -- uv sync --no-active --quiet --directory "$dir" &&
  mise -C "$dir" exec -- uv sync --no-active --quiet --directory "$dir" --project tools/notebooklm
