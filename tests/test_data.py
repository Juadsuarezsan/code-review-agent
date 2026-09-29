"""SWE-bench Lite download script tests (HTTP mocked with respx)."""

import json
from pathlib import Path

import httpx
import pytest
import respx
from pytest_mock import MockerFixture

from src.data.swebench import (
    ROWS_API,
    build_ground_truth,
    download_swebench_lite,
    extract_ground_truth,
    sha256_of,
    write_manifest,
)

PATCH = """diff --git a/django/db/models/query.py b/django/db/models/query.py
--- a/django/db/models/query.py
+++ b/django/db/models/query.py
@@ -100,3 +100,4 @@ class QuerySet:
     def only(self, *fields):
+        fields = tuple(fields)
         return self._clone()

@@ -200,2 +201,3 @@
 x = 1
+y = 2
 z = 3
"""


def _row(i: int) -> dict[str, object]:
    return {
        "row_idx": i,
        "row": {
            "instance_id": f"django__django-{i}",
            "repo": "django/django",
            "base_commit": "abc",
            "problem_statement": "bug",
            "hints_text": "",
            "patch": PATCH,
            "test_patch": "",
            "created_at": "2023",
            "version": "4.2",
            "FAIL_TO_PASS": "[]",
            "PASS_TO_PASS": "[]",
            "environment_setup_commit": "zzz",
        },
    }


@respx.mock
def test_download_paginates_and_hashes(tmp_path: Path) -> None:
    route = respx.get(ROWS_API).mock(
        side_effect=[
            httpx.Response(200, json={"rows": [_row(i) for i in range(100)], "num_rows_total": 150}),
            httpx.Response(200, json={"rows": [_row(i) for i in range(100, 150)], "num_rows_total": 150}),
        ]
    )
    report = download_swebench_lite(tmp_path / "raw")
    assert report.rows == 150 and route.call_count == 2
    assert report.sha256 == sha256_of(report.path)
    first = json.loads(report.path.read_text().splitlines()[0])
    assert first["instance_id"] == "django__django-0" and "environment_setup_commit" not in first
    assert route.calls[1].request.url.params["offset"] == "100"


@respx.mock
def test_download_limit_and_retry(tmp_path: Path, mocker: MockerFixture) -> None:
    mocker.patch("src.data.swebench.wait_exponential", return_value=lambda *_: 0)
    route = respx.get(ROWS_API).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"rows": [_row(i) for i in range(100)], "num_rows_total": 300}),
        ]
    )
    report = download_swebench_lite(tmp_path / "raw", limit=5)
    assert report.rows == 5 and route.call_count == 2


@respx.mock
def test_download_gives_up_after_retries(tmp_path: Path, mocker: MockerFixture) -> None:
    mocker.patch("src.data.swebench.wait_exponential", return_value=lambda *_: 0)
    respx.get(ROWS_API).mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        download_swebench_lite(tmp_path / "raw", max_retries=2)


def test_extract_ground_truth_hunks() -> None:
    gt = extract_ground_truth(_row(1)["row"])  # type: ignore[arg-type]
    assert gt["instance_id"] == "django__django-1" and gt["n_files"] == 1
    assert gt["locations"] == [
        {"file": "django/db/models/query.py", "new_start": 100, "new_end": 103},
        {"file": "django/db/models/query.py", "new_start": 201, "new_end": 203},
    ]
    assert extract_ground_truth({"instance_id": "x", "patch": ""})["locations"] == []


def test_build_ground_truth_and_manifest(tmp_path: Path) -> None:
    raw = tmp_path / "raw.jsonl"
    raw.write_text("\n".join(json.dumps(_row(i)["row"]) for i in range(3)) + "\n\n")
    gt = tmp_path / "processed" / "gt.jsonl"
    assert build_ground_truth(raw, gt) == 3
    manifest = tmp_path / "MANIFEST.txt"
    write_manifest(manifest, [(raw, "raw"), (gt, "gt"), (tmp_path / "missing.jsonl", "nope")], tmp_path)
    text = manifest.read_text()
    assert sha256_of(raw) in text and "  3  raw.jsonl  # raw" in text
    assert "# MISSING  missing.jsonl" in text


def test_script_manifest_only(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "download_data", Path(__file__).resolve().parents[1] / "scripts" / "download_data.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "data" / "eval").mkdir(parents=True)
    (tmp_path / "data" / "eval" / "cases.jsonl").write_text('{"id": 1}\n')
    assert module.main(["--manifest-only", "--root", str(tmp_path)]) == 0
    assert "cases.jsonl" in (tmp_path / "data" / "MANIFEST.txt").read_text()
