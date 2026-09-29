"""Evaluation harness tests: cases integrity, matching metrics, judge rubric, baselines, runner."""

import json
from pathlib import Path

import pytest

from src.api.schemas import ReviewComment
from src.config import Settings
from src.eval import runner
from src.eval.baselines import single_pass_review
from src.eval.cases import SYNTHETIC_CASES, EvalCase, GroundTruth, load_cases
from src.eval.judge import JudgeScore, build_judge_prompt, judge_comment, parse_judge_output
from src.eval.metrics import aggregate, match_case, worst_cases
from src.eval.runner import EvalConfigError, latest_runs, run_eval, write_results_md
from src.llm.client import LLMOutputError
from src.parser.diff_parser import parse_unified_diff
from tests.conftest import FakeClaudeClient


def _c(file: str, line: int, category: str = "bug") -> ReviewComment:
    return ReviewComment(file=file, line=line, severity="warning", category=category, body="x")  # type: ignore[arg-type]


# ----------------------------------------------------------------------------- cases
def test_synthetic_cases_integrity() -> None:
    cases = load_cases(SYNTHETIC_CASES)
    assert len(cases) >= 30
    assert all(c.synthetic for c in cases)
    assert sum(1 for c in cases if c.clean) >= 5
    focuses = {c.focus for c in cases}
    assert {"bug", "security", "performance", "style", "test_gap", "clean"} <= focuses
    for case in cases:
        files = {f.path: f for f in parse_unified_diff(case.diff)}
        assert files, case.id
        for gt in case.ground_truth:
            assert gt.file in files, (case.id, gt)
            assert gt.line in files[gt.file].added_line_numbers, (case.id, gt)


# --------------------------------------------------------------------------- metrics
def _case() -> EvalCase:
    return EvalCase(
        id="c1",
        title="t",
        diff="",
        ground_truth=[GroundTruth("a.py", 10, "bug"), GroundTruth("a.py", 20, "security")],
    )


def test_match_case_tolerance_and_categories() -> None:
    comments = [_c("a.py", 12, "bug"), _c("a.py", 20, "style"), _c("b.py", 10, "bug")]
    r = match_case(_case(), comments, latency_ms=5, cost_usd=0.0)
    assert [t.line for t in r.matched_truth] == [10]
    assert [t.line for t in r.missed_truth] == [20]
    assert len(r.true_positive_comments) == 1 and len(r.false_positive_comments) == 2
    assert r.recall == 0.5 and r.precision == pytest.approx(1 / 3)
    assert r.severity_score() == 2.0 + 2.0
    empty = match_case(EvalCase("c2", "t", ""), [], latency_ms=1, cost_usd=0.0)
    assert empty.recall is None and empty.precision is None and empty.clean


def test_aggregate_and_worst_cases() -> None:
    r1 = match_case(
        _case(), [_c("a.py", 10), _c("a.py", 20, "security")], latency_ms=10, cost_usd=0.01
    )
    r2 = match_case(_case(), [_c("a.py", 99)], latency_ms=30, cost_usd=0.03)
    clean = match_case(EvalCase("c3", "t", ""), [_c("z.py", 1)], latency_ms=20, cost_usd=0.0)
    agg = aggregate([r1, r2, clean])
    assert agg.n_ground_truth == 4 and agg.matched_truth == 2
    assert agg.recall == 0.5 and agg.precision == 0.5 and agg.f1 == 0.5
    assert agg.fp_rate == 0.5 and agg.comments_per_clean_pr == 1.0
    assert agg.mean_latency_ms == 20 and agg.p95_latency_ms == 30
    assert (
        agg.per_category["bug"]["recall"] == 0.5 and agg.per_category["security"]["recall"] == 0.5
    )
    assert [w.case_id for w in worst_cases([r1, r2, clean], n=2)] == ["c1", "c3"] or worst_cases(
        [r1, r2, clean]
    )[0] is r2
    assert aggregate([]).recall == 0.0


# ----------------------------------------------------------------------------- judge
def test_parse_judge_output_validation() -> None:
    score = parse_judge_output(
        {"actionability": 5, "specificity": 3, "correctness": 4, "rationale": "ok"}
    )
    assert score == JudgeScore(5, 3, 4, "ok") and score.quality == pytest.approx(0.75)
    with pytest.raises(LLMOutputError):
        parse_judge_output({"actionability": 6, "specificity": 3, "correctness": 4})
    with pytest.raises(LLMOutputError):
        parse_judge_output({"actionability": "high", "specificity": 3, "correctness": 4})


async def test_judge_comment_with_fake_client() -> None:
    client = FakeClaudeClient(
        responses=[{"actionability": 4, "specificity": 4, "correctness": 5, "rationale": "r"}]
    )
    score, usage = await judge_comment(client, _c("a.py", 1), "diff text")
    assert score.correctness == 5 and usage.cost_usd > 0
    assert (
        "<comment>" in build_judge_prompt(_c("a.py", 1), "d")
        and "ACTIONABILITY" in client.calls[0][0]
    )


# ------------------------------------------------------------------------- baselines
DIFF = "--- a/m.py\n+++ b/m.py\n@@ -1,1 +1,3 @@\n def f(x):\n+    y = x\n+    return eval(y)\n"


async def test_single_pass_review() -> None:
    client = FakeClaudeClient(
        responses=[
            {
                "comments": [
                    {
                        "file": "m.py",
                        "line": 3,
                        "severity": "error",
                        "category": "security",
                        "body": "eval",
                    },
                    {
                        "file": "other.py",
                        "line": 1,
                        "severity": "info",
                        "category": "style",
                        "body": "dropped",
                    },
                    "junk",
                ]
            }
        ]
    )
    comments, usage, errors = await single_pass_review(client, DIFF)
    assert [c.line for c in comments] == [3] and comments[0].source == "claude-single-pass"
    assert usage is not None and errors == []
    comments, usage, errors = await single_pass_review(FakeClaudeClient(responses=["nope"]), DIFF)
    assert comments == [] and usage is None and errors and "single-pass" in errors[0]


# ---------------------------------------------------------------------------- runner
@pytest.fixture
def eval_dirs(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "runs", tmp_path / "RESULTS.md"


async def test_run_eval_linter_only_writes_run_and_results(
    settings: Settings, eval_dirs: tuple[Path, Path]
) -> None:
    runs_dir, results_md = eval_dirs
    run = await run_eval("linter-only", settings, runs_dir=runs_dir, results_md=results_md, limit=3)
    assert run["mode"] == "linter-only" and run["llm_used"] is False
    assert run["dataset"]["n_cases"] == 3 and run["dataset"]["synthetic"] is True
    files = list(runs_dir.glob("*-linter-only.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["aggregate"]["n_cases"] == 3
    text = results_md.read_text()
    assert "| Sistema | Bug Recall |" in text
    assert "Linter only" in text and "$0 (sin LLM)" in text
    assert (
        text.count("pendiente (requiere ANTHROPIC_API_KEY)") >= 9
    )  # two pending rows + judge cell
    assert "10 peores casos" in text
    assert latest_runs(runs_dir)["linter-only"]["run_file"] == files[0].name


async def test_run_eval_requires_key_for_llm_modes(
    settings: Settings, eval_dirs: tuple[Path, Path]
) -> None:
    runs_dir, results_md = eval_dirs
    with pytest.raises(EvalConfigError):
        await run_eval("pipeline", settings, runs_dir=runs_dir, results_md=results_md, limit=1)
    with pytest.raises(EvalConfigError):
        await run_eval("bogus", settings, runs_dir=runs_dir, results_md=results_md)


async def test_run_eval_llm_modes_with_fake_client(
    settings: Settings, eval_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    runs_dir, results_md = eval_dirs
    settings = settings.model_copy(update={"anthropic_api_key": "test-key"})
    judge_answer = {"actionability": 4, "specificity": 4, "correctness": 4, "rationale": "r"}
    default = {"comments": [], **judge_answer}
    monkeypatch.setattr(runner, "ClaudeClient", lambda *a, **k: FakeClaudeClient(default=default))
    run = await run_eval(
        "single-pass", settings, runs_dir=runs_dir, results_md=results_md, limit=2, judge=True
    )
    assert run["llm_used"] and run["model"] == settings.anthropic_model
    assert run["judge"]["n_scored"] == 0  # no comments produced by the empty default answer
    run = await run_eval(
        "pipeline", settings, runs_dir=runs_dir, results_md=results_md, limit=2, judge=True
    )
    assert run["judge"]["n_scored"] > 0 and run["judge"]["comment_quality"] == pytest.approx(0.75)
    text = write_results_md(runs_dir, results_md)
    assert "Claude single-pass" in text and "Pipeline completo" in text and "(n=" in text


def test_main_results_only_and_run(
    eval_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    runs_dir, results_md = eval_dirs
    monkeypatch.setattr(runner, "RUNS_DIR", runs_dir)
    monkeypatch.setattr(runner, "RESULTS_MD", results_md)
    assert runner.main(["--results-only"]) == 0
    assert results_md.exists()
    assert runner.main(["--mode", "linter-only", "--limit", "1", "--name", "smoke"]) == 0
    assert list(runs_dir.glob("*-smoke.json"))
    assert runner.main(["--mode", "pipeline", "--limit", "1"]) == 2


def test_latest_runs_skips_bad_json(tmp_path: Path) -> None:
    (tmp_path / "2026-01-01-linter-only.json").write_text("{bad")
    assert latest_runs(tmp_path) == {}
