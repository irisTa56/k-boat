#!/bin/sh
# PreToolUse hook (.claude/settings.json) on the browser tools. CLAUDE.md
# "Delegation" says why a subagent gets none of them.
#
# Exit 2 is the code that blocks the call, so a failed read blocks it too: an
# empty read would otherwise pass as the main conversation, which has no
# `agent_id`.

id=$(jq -r '.agent_id // empty') || {
  echo "Blocked: could not read the hook input with jq." >&2
  exit 2
}
[ -n "$id" ] || exit 0

echo "Blocked: a subagent in this project may not use a browser. Read the page with WebFetch." >&2
exit 2
