"""Top-level download orchestrator.

Each source lives in its own submodule (sabs, tiger, acs, ccd) and exposes
``fetch(force: bool = False) -> list[tuple[label, status, path]]``. The
orchestrator iterates over them, collects results, and prints a status
table. Re-running is a no-op unless ``--force`` is passed: each fetcher
short-circuits when its output file already exists.

Run via ``python -m pipeline.download [--force]``.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from pipeline.download import acs, ccd, sabs, tiger

log = logging.getLogger(__name__)

MODULES = (sabs, tiger, acs, ccd)

Result = tuple[str, str, Path | None]


def fetch_all(force: bool = False) -> list[Result]:
    results: list[Result] = []
    for module in MODULES:
        try:
            results.extend(module.fetch(force=force))
        except Exception as exc:
            log.exception("Fetcher %s failed", module.__name__)
            results.append((module.__name__, f"error: {exc}", None))
    return results


def print_status_table(results: list[Result]) -> None:
    if not results:
        print("(no fetchers ran)")
        return
    label_width = max(len(label) for label, _, _ in results)
    status_width = max(len(status) for _, status, _ in results)
    for label, status, path in results:
        path_str = str(path) if path is not None else "—"
        print(f"  {status:<{status_width}}  {label:<{label_width}}  ->  {path_str}")


def main(force: bool = False) -> int:
    results = fetch_all(force=force)
    print_status_table(results)
    ok = all(status in ("downloaded", "skipped") for _, status, _ in results)
    return 0 if ok else 1


def cli() -> None:
    parser = argparse.ArgumentParser(
        prog="pipeline.download",
        description=(
            "Fetch all raw inputs (NCES SABS, TIGER block groups + blocks, "
            "ACS B19131/B11005/B19013, NCES CCD). Idempotent — re-runs skip "
            "files that already exist."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the output file already exists.",
    )
    args = parser.parse_args()
    sys.exit(main(force=args.force))


if __name__ == "__main__":
    cli()
