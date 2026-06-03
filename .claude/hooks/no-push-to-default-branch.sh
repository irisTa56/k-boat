#!/usr/bin/env bash
# PreToolUse(Bash) guard: refuse `git push` while HEAD is on the default branch
# (main/master). The default branch should only advance via a feature branch + PR.
#
# Best-effort, agent-side accident guard. Uses only grep + git (both always present)
# so a missing dependency cannot silently disable it. Reads the hook JSON on stdin.

input=$(cat)

# Pull out just the Bash tool's `command` value, so a `description` (or any other
# field) that merely mentions "git push" cannot trigger a false deny. Plain POSIX
# grep — no jq/python, hence no dependency that could be absent and fail open. The
# `[^"]*` stops at the first quote, which also harmlessly truncates commands with
# embedded quotes (e.g. `git commit -m "push"`) before any spurious match.
cmd=$(printf '%s' "$input" | grep -Eo '"command"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n 1)

# Match a `git push` invocation in that command: `git`, then zero or more global
# options (e.g. `-C <dir>`, `-c k=v`, `--git-dir=...`), then the `push` subcommand.
# Requiring options to start with `-` avoids matching `git status && echo push` or
# `git commit -m "push"`; the boundaries avoid substrings like `mygit pushy`.
push_re='(^|[^[:alnum:]_./-])git([[:space:]]+-{1,2}[^[:space:]]*([[:space:]]+[^[:space:]-][^[:space:]]*)?)*[[:space:]]+push([^[:alnum:]_-]|$)'

if printf '%s' "$cmd" | grep -Eq "$push_re"; then
  branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
  if [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Refusing git push from the default branch (%s). Create a feature branch (git checkout -b <name>) and open a PR instead."}}\n' "$branch"
  fi
fi
exit 0
