"""Contract test for the console-script entrypoint.

Phase 1 ships `feed-filter`'s target as a placeholder; the real CLI dispatch
lands in Phase 5. This pins the current behavior (importable module, callable
target that fails loudly) and is updated in lockstep when Phase 5 wires the CLI.
"""

from __future__ import annotations

import pytest

from feed_filter.__main__ import main


def test_entrypoint_not_yet_wired() -> None:
    with pytest.raises(NotImplementedError):
        main()
