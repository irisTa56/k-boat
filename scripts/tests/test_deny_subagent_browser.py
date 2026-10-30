"""Tests for `scripts/deny_subagent_browser.py`.

Exit 2 is what makes a `PreToolUse` hook block the call; any other non-zero exit
lets the call through, so each test pins the exact code. `conftest.py` puts
`scripts/` on `sys.path` so the import below resolves.
"""

import io
import json

import deny_subagent_browser
import pytest


def test_a_subagents_call_is_blocked_with_a_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {"tool_name": "mcp__claude-in-chrome__navigate", "agent_id": "a1"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert deny_subagent_browser.main() == 2
    assert "subagent" in capsys.readouterr().err


def test_the_main_conversations_call_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"tool_name": "mcp__Claude_Browser__navigate"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert deny_subagent_browser.main() == 0
