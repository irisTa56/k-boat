"""Tests for `scripts/deny_subagent_browser.sh`, run as Claude Code runs the hook.

Exit 2 is what makes a `PreToolUse` hook block the call; any other non-zero exit
lets the call through, so each test pins the exact code.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "deny_subagent_browser.sh"


def _run(
    payload: dict[str, str], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_a_subagents_call_is_blocked_with_a_reason() -> None:
    result = _run({"tool_name": "mcp__claude-in-chrome__navigate", "agent_id": "a1"})
    assert result.returncode == 2
    assert "subagent" in result.stderr


def test_the_main_conversations_call_is_left_alone() -> None:
    result = _run({"tool_name": "mcp__Claude_Browser__navigate"})
    assert result.returncode == 0


def test_a_call_it_cannot_read_is_blocked(tmp_path: Path) -> None:
    # Reading the input as empty would pass as the main conversation, which is
    # how a subagent's call would slip through wherever `jq` is not on `PATH`.
    result = _run(
        {"tool_name": "mcp__claude-in-chrome__navigate", "agent_id": "a1"},
        env={"PATH": str(tmp_path)},
    )
    assert result.returncode == 2
    assert "jq" in result.stderr
