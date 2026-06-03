#!/usr/bin/env bash
# PreToolUse(Bash) guard: refuse `git push` while HEAD is on the default branch
# (main/master). The default branch should only advance via a feature branch + PR.
#
# Best-effort, agent-side accident guard. Uses only grep + git (both always present)
# so a missing dependency cannot silently disable it — the earlier jq-based version
# failed open when jq was absent. Reads the hook JSON on stdin and matches the raw
# payload directly; emits a deny decision only when a push from main/master is seen,
# otherwise stays silent and allows the command.

input=$(cat)

# Match a `git push` invocation inside the (possibly compound) command: `git`, then
# zero or more global options (e.g. `-C <dir>`, `-c k=v`, `--git-dir=...`), then the
# `push` subcommand. The leading/trailing boundaries keep it from matching substrings
# like `mygit pushy`, while requiring options to start with `-` avoids matching
# `git status && echo push` or `git commit -m "push"`.
push_re='(^|[^[:alnum:]_./-])git([[:space:]]+-{1,2}[^[:space:]]*([[:space:]]+[^[:space:]-][^[:space:]]*)?)*[[:space:]]+push([^[:alnum:]_-]|$)'

if printf '%s' "$input" | grep -Eq "$push_re"; then
  branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
  if [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Refusing git push from the default branch (%s). Create a feature branch (git checkout -b <name>) and open a PR instead."}}\n' "$branch"
  fi
fi
exit 0
