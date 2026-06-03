#!/usr/bin/env bash
# PreToolUse(Bash) guard: refuse `git push` while HEAD is on the default branch
# (main/master). The default branch should only advance via a feature branch + PR.
# Reads the hook JSON on stdin; emits a deny decision only when both conditions hold,
# otherwise stays silent and allows the command.

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""' 2>/dev/null)

# Match an actual `git push` token (git, whitespace, push), even inside a compound
# command like `cd x && git push`; do not match `git commit -m "push"` etc.
if printf '%s' "$cmd" | grep -Eq '(^|[^[:alnum:]_./-])git[[:space:]]+push([[:space:]]|$)'; then
  branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
  if [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Refusing git push from the default branch (%s). Create a feature branch (git checkout -b <name>) and open a PR instead."}}\n' "$branch"
  fi
fi
exit 0
