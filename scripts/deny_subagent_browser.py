"""`PreToolUse` hook (`.claude/settings.json`) that blocks the browser tools in a subagent.

CLAUDE.md "Delegation" says why a subagent gets none of them. Claude Code sets
`agent_id` in the hook input only inside a subagent, and exit 2 is the code that
blocks the call.

Stdlib-only, so the hook's start-up stays at the interpreter's own.
"""

import json
import sys


def main() -> int:
    if not json.load(sys.stdin).get("agent_id"):
        return 0

    print(
        "Blocked: a subagent in this project may not use a browser. Read the page with WebFetch.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
