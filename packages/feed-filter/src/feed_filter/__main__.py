"""Console-script entrypoint: `feed-filter ...` dispatches to the CLI."""

from feed_filter import cli


def main() -> int:
    return cli.main()


if __name__ == "__main__":
    raise SystemExit(main())
