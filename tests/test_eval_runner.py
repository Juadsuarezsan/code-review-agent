import pytest

from src.eval.runner import run_eval


@pytest.mark.asyncio
async def test_eval_recall_acceptable_with_static_rules_only():
    report = await run_eval()
    assert report["n"] == 8
    # Static rules alone should catch the bug/security/style/test_gap categories.
    assert report["recall"] >= 0.50
