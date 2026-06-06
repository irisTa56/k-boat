# Feed Filter

A simplified, Claude-Code-native reimplementation of [`loose-feeds`](../loose-feeds).

A local Claude Code scheduled routine periodically discovers new pages from registered sites, filters them against a prompt using cheap subagents, and pushes the survivors into the macOS Reminders list **`Filtered Feeds`** via the [`rem`](https://github.com/BRO3886/rem) CLI.

## Design

Responsibilities split deterministically.
Plain Python owns everything verifiable and cheap — fetching, feed/scrape parsing, discovery, URL canonicalization, the seen-store, and the `rem` wrapper — exposed as a single `feed-filter` CLI.
The LLM owns only the two genuinely fuzzy judgments: picking the article cluster during site registration, and per-page keep/drop selection.

See [plan/feature-feed-filter-1.md](plan/feature-feed-filter-1.md) for the full specification and phased implementation plan.

## Development

```sh
mise install          # install tools + generate the git pre-commit hook
mise run pre-commit   # run the full QA gate (Python + markdown + secrets)
mise run qa:py:test   # run the Python test suite
```

## Status

Under construction. The project scaffold and QA gate are in place; ingestion, discovery, the CLI surface, and the Claude Code skills land in subsequent phases.
