"""Console-script entrypoint: `feed-filter ...` dispatches to the CLI.

The real subcommand dispatch lands in `feed_filter.cli` (Phase 5). Until then
`main` is a placeholder that fails loudly; `tests/test_main.py` pins that
contract so the entrypoint stays importable and the Phase-5 wiring has a
regression anchor.
"""

from __future__ import annotations


def main() -> int:
    # Replaced in Phase 5 with `from feed_filter import cli; return cli.main()`.
    raise NotImplementedError("CLI dispatch lands in Phase 5")


if __name__ == "__main__":
    raise SystemExit(main())
