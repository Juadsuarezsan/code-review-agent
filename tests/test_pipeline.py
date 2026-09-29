"""End-to-end tests of the LangGraph review pipeline (deterministic and mocked-LLM modes)."""

import anthropic
import httpx

from src.config import Settings
from src.graph.pipeline import NODE_NAMES, ReviewPipeline
from tests.conftest import FakeClaudeClient

PR_DIFF = """diff --git a/app/service.py b/app/service.py
--- a/app/service.py
+++ b/app/service.py
@@ -1,5 +1,14 @@
 import os
 import subprocess

 def run(cmd, items=[]):
-    return subprocess.run(cmd)
+    out = ""
+    for item in items:
+        out += str(item)
+    try:
+        return os.system(cmd)
+    except:
+        return None
+
+def helper(x):
+    return x == None
diff --git a/tests/test_service.py b/tests/test_service.py
--- a/tests/test_service.py
+++ b/tests/test_service.py
@@ -1,1 +1,3 @@
 import pytest
+def test_run():
+    assert run("ls") is None
"""


async def test_linter_only_end_to_end(settings: Settings) -> None:
    pipeline = ReviewPipeline(settings, use_llm=False)
    assert pipeline.mode == "linter-only"
    result = await pipeline.run(PR_DIFF, max_comments=20)
    categories = {c.category for c in result.comments}
    rule_ids = {c.rule_id or "" for c in result.comments}
    assert {"security", "bug", "performance", "style", "test_gap"} <= categories
    assert any("shell-injection-system" in r for r in rule_ids)
    assert any("bare-except" in r for r in rule_ids)
    assert any("quadratic-str-concat" in r for r in rule_ids)
    # helper() is untested; run() is referenced by the test change.
    gaps = [c for c in result.comments if c.category == "test_gap"]
    assert [c.line for c in gaps] == [13]
    assert result.metrics.llm_calls == 0 and result.metrics.cost_usd == 0.0
    assert {t.node for t in result.metrics.node_timings} == set(NODE_NAMES)
    assert result.summary.startswith(f"{len(result.comments)} comment(s)")
    assert result.n_lines_added == 12 and result.n_lines_removed == 1
    assert result.errors == []


async def test_priority_cap_is_respected(settings: Settings) -> None:
    result = await ReviewPipeline(settings, use_llm=False).run(PR_DIFF, max_comments=3)
    assert len(result.comments) == 3
    # both errors (bare except, os.system) must survive the cap
    assert sum(c.severity == "error" for c in result.comments) == 2


async def test_pipeline_with_mocked_claude(settings: Settings) -> None:
    bug_payload = {
        "comments": [
            {
                "file": "app/service.py",
                "line": 10,
                "severity": "error",
                "category": "bug",
                "body": "os.system return code is not an exception",
                "confidence": 0.9,
            }
        ]
    }
    synth_payload = {
        "comments": [
            {
                "file": "app/service.py",
                "line": 10,
                "severity": "error",
                "body": "Rewritten by model",
                "justification": "j",
            }
        ]
    }
    client = FakeClaudeClient(responses=[bug_payload, {"comments": []}, synth_payload])
    pipeline = ReviewPipeline(settings, client=client)
    assert pipeline.mode == "pipeline"
    result = await pipeline.run(PR_DIFF, max_comments=20)
    assert result.mode == "pipeline"
    assert result.metrics.llm_calls == 3 and result.metrics.cost_usd > 0
    assert result.metrics.input_tokens > 0
    assert any(
        c.body == "Rewritten by model." or c.body == "Rewritten by model" for c in result.comments
    )
    # the model comment merges with the static rule on the same line; both rule ids survive
    assert any("claude:bug" in (c.rule_id or "") for c in result.comments)


async def test_llm_failures_are_recorded_not_raised(settings: Settings) -> None:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.BadRequestError("bad", response=httpx.Response(400, request=request), body=None)
    client = FakeClaudeClient(responses=[err, "not json", "still not json"])
    result = await ReviewPipeline(settings, client=client).run(PR_DIFF, max_comments=20)
    assert len(result.errors) == 2
    assert any("BadRequestError" in e for e in result.errors)
    assert any("LLMOutputError" in e for e in result.errors)
    # deterministic findings survive
    assert any(c.category == "security" for c in result.comments)


async def test_empty_diff_yields_no_comments(settings: Settings) -> None:
    result = await ReviewPipeline(settings, use_llm=False).run("")
    assert result.comments == [] and result.files == []
    assert result.summary == "Reviewed 0 file(s); no issues flagged."
