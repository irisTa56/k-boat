"""Contract test for the console-script entrypoint.

`feed-filter`'s target now delegates to the Phase-5 CLI dispatch; this pins that
the entrypoint forwards to `cli.main` and returns its exit code.
"""

from __future__ import annotations

import pytest

import feed_filter.__main__ as entry


def test_entrypoint_delegates_to_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    def fake_cli_main() -> int:
        called.append(True)
        return 0

    monkeypatch.setattr(entry.cli, "main", fake_cli_main)
    assert entry.main() == 0
    assert called == [True]
