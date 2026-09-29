#!/usr/bin/env python
"""Download SWE-bench Lite (300 issues) and refresh ``data/MANIFEST.txt``.

Usage::

    python scripts/download_data.py                 # full download + ground truth + manifest
    python scripts/download_data.py --limit 20      # smoke test
    python scripts/download_data.py --manifest-only # only rehash committed data files

Source: https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite (test split, 300 rows).
The raw file lands in ``data/raw/`` (git-ignored); the derived ground truth in
``data/processed/`` (git-ignored); both are recorded in ``data/MANIFEST.txt``.

NOTE: in the environment where this project was developed ``huggingface.co`` is not
reachable, so the script is exercised by ``tests/test_data.py`` with mocked HTTP and the
real download must be run elsewhere.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from loguru import logger  # noqa: E402

from src.data.swebench import (  # noqa: E402
    GT_FILENAME,
    RAW_FILENAME,
    build_ground_truth,
    download_swebench_lite,
    write_manifest,
)


def refresh_manifest(root: Path) -> None:
    """Rehash every data artefact and rewrite the manifest."""
    write_manifest(
        root / "data" / "MANIFEST.txt",
        [
            (root / "data" / "eval" / "cases.jsonl", "synthetic eval set, hand-written GT"),
            (root / "data" / "raw" / RAW_FILENAME, "SWE-bench Lite test split (download)"),
            (root / "data" / "processed" / GT_FILENAME, "hunk-level GT derived from patch"),
        ],
        root,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--manifest-only", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    root: Path = args.root
    if not args.manifest_only:
        report = download_swebench_lite(root / "data" / "raw", limit=args.limit)
        logger.info("saved {} rows to {} sha256={}", report.rows, report.path, report.sha256)
        n = build_ground_truth(report.path, root / "data" / "processed" / GT_FILENAME)
        logger.info("ground truth written for {} instances", n)
    refresh_manifest(root)
    logger.info("manifest updated: {}", root / "data" / "MANIFEST.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
