"""SWE-bench Lite download and ground-truth extraction.

The dataset is fetched page by page from the Hugging Face ``datasets-server``
rows API (no ``datasets``/``pyarrow`` dependency). Each row keeps the full
``patch`` and ``test_patch`` so that bug locations (file + hunk) can be derived.

Ground-truth criterion (used by ``eval``): a review comment matches a SWE-bench
instance when it points at a file touched by the golden ``patch`` and its line
falls inside one of the patch's new-side hunks (``new_start .. new_start+new_len``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

DATASET = "princeton-nlp/SWE-bench_Lite"
DATASET_URL = f"https://huggingface.co/datasets/{DATASET}"
ROWS_API = "https://datasets-server.huggingface.co/rows"
PAGE_SIZE = 100
RAW_FILENAME = "swebench_lite_test.jsonl"
GT_FILENAME = "swebench_lite_ground_truth.jsonl"
KEEP_FIELDS = (
    "instance_id",
    "repo",
    "base_commit",
    "problem_statement",
    "hints_text",
    "patch",
    "test_patch",
    "created_at",
    "version",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
)


@dataclass(frozen=True)
class DownloadReport:
    """Outcome of :func:`download_swebench_lite`."""

    path: Path
    rows: int
    sha256: str


def sha256_of(path: Path) -> str:
    """Hex SHA-256 of a file, streamed."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_page(client: httpx.Client, offset: int, length: int, max_retries: int) -> dict[str, Any]:
    params: dict[str, str | int] = {
        "dataset": DATASET,
        "config": "default",
        "split": "test",
        "offset": offset,
        "length": length,
    }
    for attempt in Retrying(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        stop=stop_after_attempt(max_retries),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    ):
        with attempt:
            response = client.get(ROWS_API, params=params)
            if response.status_code == 429 or response.status_code >= 500:
                response.raise_for_status()
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return data
    raise RuntimeError("unreachable")  # pragma: no cover


def download_swebench_lite(
    out_dir: Path,
    *,
    limit: int | None = None,
    timeout_seconds: float = 60.0,
    max_retries: int = 3,
    client: httpx.Client | None = None,
) -> DownloadReport:
    """Download the SWE-bench Lite test split to ``out_dir/swebench_lite_test.jsonl``.

    Args:
        out_dir: Destination directory (created if missing).
        limit: Stop after this many rows (for smoke tests).
        timeout_seconds: HTTP timeout per page.
        max_retries: Retries per page on transient failures.
        client: Optional pre-built ``httpx.Client`` (tests inject a mocked one).

    Returns:
        A :class:`DownloadReport` with the row count and SHA-256 of the file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / RAW_FILENAME
    own_client = client is None
    client = client or httpx.Client(timeout=timeout_seconds)
    rows_written = 0
    try:
        with path.open("w", encoding="utf-8") as fh:
            offset = 0
            total: int | None = None
            while total is None or offset < total:
                page = _fetch_page(client, offset, PAGE_SIZE, max_retries)
                total = int(page.get("num_rows_total", 0))
                rows = page.get("rows", [])
                if not rows:
                    break
                for item in rows:
                    row = item.get("row", item)
                    record = {k: row.get(k) for k in KEEP_FIELDS}
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                    rows_written += 1
                    if limit is not None and rows_written >= limit:
                        break
                if limit is not None and rows_written >= limit:
                    break
                offset += len(rows)
                logger.info("downloaded {}/{} rows", rows_written, total)
    finally:
        if own_client:
            client.close()
    return DownloadReport(path=path, rows=rows_written, sha256=sha256_of(path))


def extract_ground_truth(instance: dict[str, Any]) -> dict[str, Any]:
    """Derive bug locations from an instance's golden ``patch``.

    Returns a dict with ``instance_id``, ``repo`` and ``locations``: one entry per
    hunk with ``file``, ``new_start`` and ``new_end`` (inclusive, new-side lines).
    """
    from src.parser.diff_parser import parse_unified_diff

    locations: list[dict[str, Any]] = []
    for fd in parse_unified_diff(instance.get("patch") or ""):
        for hunk in fd.hunks:
            locations.append(
                {
                    "file": fd.path,
                    "new_start": hunk.new_start,
                    "new_end": hunk.new_start + max(hunk.new_len, 1) - 1,
                }
            )
    return {
        "instance_id": instance.get("instance_id"),
        "repo": instance.get("repo"),
        "n_files": len({loc["file"] for loc in locations}),
        "locations": locations,
    }


def build_ground_truth(raw_path: Path, out_path: Path) -> int:
    """Write one ground-truth record per instance; returns the record count."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with raw_path.open(encoding="utf-8") as src, out_path.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            dst.write(json.dumps(extract_ground_truth(json.loads(line))) + "\n")
            count += 1
    return count


def write_manifest(manifest_path: Path, entries: list[tuple[Path, str]], root: Path) -> None:
    """Write ``data/MANIFEST.txt`` with one line per file: sha256, size, rows, relative path.

    ``entries`` pairs a file path with a short provenance note.
    """
    lines = [
        "# Data manifest — regenerate with: python scripts/download_data.py --manifest-only",
        "# sha256  bytes  rows  path  # note",
    ]
    for path, note in entries:
        if not path.exists():
            lines.append(f"# MISSING  {path.relative_to(root)}  # {note}")
            continue
        rows = 0
        if path.suffix in (".jsonl", ".txt"):
            with path.open(encoding="utf-8") as fh:
                rows = sum(1 for line in fh if line.strip())
        lines.append(
            f"{sha256_of(path)}  {path.stat().st_size}  {rows}  {path.relative_to(root)}  # {note}"
        )
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
