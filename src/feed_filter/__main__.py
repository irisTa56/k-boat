"""Console-script entrypoint: `feed-filter ...` dispatches to the CLI.

The real subcommand dispatch lands in `feed_filter.cli` (Phase 5). Until then
`main` is a placeholder; it is excluded from coverage because there is no CLI
surface to exercise yet.
"""

from __future__ import annotations


def main() -> int:  # pragma: no cover
    # Replaced in Phase 5 with `from feed_filter import cli; return cli.main()`.
    raise NotImplementedError("CLI dispatch lands in Phase 5")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
