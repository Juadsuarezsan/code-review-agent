"""Evaluation runner: ``python -m eval.run --mode linter-only|single-pass|pipeline``.

Each run writes ``eval/runs/<YYYY-MM-DD>-<mode>.json`` and regenerates
``eval/RESULTS.md`` from the latest run of every system. Cells whose system has
no committed run are printed as ``pendiente (requiere ANTHROPIC_API_KEY)`` —
numbers are never invented.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from src.api.schemas import ReviewComment
from src.config import Settings
from src.eval.baselines import single_pass_review
from src.eval.cases import ROOT, SYNTHETIC_CASES, EvalCase, load_cases
from src.eval.judge import judge_comment
from src.eval.metrics import CaseResult, aggregate, match_case, worst_cases
from src.graph.pipeline import ReviewPipeline
from src.llm.client import ClaudeClient, LLMOutputError
from src.observability import configure_logging

RUNS_DIR = ROOT / "eval" / "runs"
RESULTS_MD = ROOT / "eval" / "RESULTS.md"
MODES = ("linter-only", "single-pass", "pipeline")
SYSTEM_LABELS = {
    "linter-only": (
        "Linter only (reglas estáticas + ruff + patrones; fallback determinista, sin LLM)"
    ),
    "single-pass": "Claude single-pass",
    "pipeline": "Pipeline completo (este)",
}
PENDING = "pendiente (requiere ANTHROPIC_API_KEY)"
JUDGE_SAMPLE = 100


class EvalConfigError(RuntimeError):
    """Raised when a mode cannot run in the current environment."""


def _serialize_result(r: CaseResult) -> dict[str, Any]:
    return {
        "case_id": r.case_id,
        "title": r.title,
        "clean": r.clean,
        "n_ground_truth": r.n_ground_truth,
        "n_comments": r.n_comments,
        "recall": r.recall,
        "precision": r.precision,
        "matched": [asdict(t) for t in r.matched_truth],
        "missed": [asdict(t) for t in r.missed_truth],
        "false_positives": [c.model_dump() for c in r.false_positive_comments],
        "true_positives": [c.model_dump() for c in r.true_positive_comments],
        "latency_ms": r.latency_ms,
        "cost_usd": r.cost_usd,
        "errors": r.errors,
    }


async def _review_case(
    mode: str, case: EvalCase, pipeline: ReviewPipeline | None, client: ClaudeClient | None
) -> tuple[list[ReviewComment], int, float, list[str]]:
    if mode == "single-pass":
        assert client is not None
        import time

        t0 = time.perf_counter()
        comments, usage, errors = await single_pass_review(client, case.diff)
        latency = int((time.perf_counter() - t0) * 1000)
        return comments, latency, usage.cost_usd if usage else 0.0, errors
    assert pipeline is not None
    result = await pipeline.run(case.diff, max_comments=10)
    return result.comments, result.metrics.latency_ms, result.metrics.cost_usd, result.errors


async def _judge_sample(
    client: ClaudeClient, results: list[CaseResult], cases: dict[str, EvalCase]
) -> dict[str, Any]:
    """Score up to :data:`JUDGE_SAMPLE` comments with the rubric."""
    scored: list[dict[str, Any]] = []
    cost = 0.0
    for r in results:
        for c in r.true_positive_comments + r.false_positive_comments:
            if len(scored) >= JUDGE_SAMPLE:
                break
            try:
                score, usage = await judge_comment(client, c, cases[r.case_id].diff)
            except LLMOutputError as exc:
                logger.error("judge failed case={} err={}", r.case_id, exc)
                continue
            cost += usage.cost_usd
            scored.append({"case_id": r.case_id, "file": c.file, "line": c.line, **asdict(score)})
    quality = (
        sum(
            ((s["actionability"] + s["specificity"] + s["correctness"]) / 3 - 1) / 4 for s in scored
        )
        / len(scored)
        if scored
        else None
    )
    return {
        "n_scored": len(scored),
        "comment_quality": quality,
        "judge_cost_usd": cost,
        "scores": scored,
    }


async def run_eval(
    mode: str,
    settings: Settings,
    *,
    cases_path: Path = SYNTHETIC_CASES,
    runs_dir: Path = RUNS_DIR,
    results_md: Path = RESULTS_MD,
    judge: bool = False,
    limit: int | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Run one system over the cases, persist the run and regenerate RESULTS.md."""
    if mode not in MODES:
        raise EvalConfigError(f"unknown mode {mode!r}; choose from {MODES}")
    cases = load_cases(cases_path)
    if limit:
        cases = cases[:limit]
    client: ClaudeClient | None = None
    pipeline: ReviewPipeline | None = None
    if mode == "linter-only":
        pipeline = ReviewPipeline(settings, use_llm=False)
    else:
        if not settings.llm_enabled:
            raise EvalConfigError(f"mode {mode!r} requires ANTHROPIC_API_KEY")
        client = ClaudeClient(
            settings.anthropic_api_key,
            settings.anthropic_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            max_tokens=settings.llm_max_tokens,
        )
        if mode == "pipeline":
            pipeline = ReviewPipeline(settings, client=client)

    results: list[CaseResult] = []
    for case in cases:
        comments, latency, cost, errors = await _review_case(mode, case, pipeline, client)
        results.append(match_case(case, comments, latency_ms=latency, cost_usd=cost, errors=errors))
        logger.info(
            "case={} gt={} comments={} matched={} fp={}",
            case.id,
            len(case.ground_truth),
            len(comments),
            len(results[-1].matched_truth),
            len(results[-1].false_positive_comments),
        )
    agg = aggregate(results)
    judge_block: dict[str, Any] | None = None
    if judge:
        if client is None:
            raise EvalConfigError("--judge requires ANTHROPIC_API_KEY")
        judge_block = await _judge_sample(client, results, {c.id: c for c in cases})

    run = {
        "mode": mode,
        "system": SYSTEM_LABELS[mode],
        "llm_used": mode != "linter-only",
        "model": settings.anthropic_model if mode != "linter-only" else None,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": {
            "path": (
                str(cases_path.relative_to(ROOT))
                if cases_path.is_relative_to(ROOT)
                else str(cases_path)
            ),
            "n_cases": len(cases),
            "synthetic": all(c.synthetic for c in cases),
            "n_ground_truth": agg.n_ground_truth,
            "matching": "same file, same category, |line - gt_line| <= 2",
        },
        "aggregate": asdict(agg),
        "judge": judge_block,
        "worst_cases": [_serialize_result(r) for r in worst_cases(results)],
        "cases": [_serialize_result(r) for r in results],
    }
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out = runs_dir / f"{stamp}-{name or mode}.json"
    out.write_text(json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")
    run["run_file"] = str(out)
    write_results_md(runs_dir, results_md)
    return run


def latest_runs(runs_dir: Path) -> dict[str, dict[str, Any]]:
    """Latest committed run per mode (by file name order, i.e. date)."""
    latest: dict[str, dict[str, Any]] = {}
    for path in sorted(runs_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("skipping unreadable run {}: {}", path, exc)
            continue
        mode = data.get("mode")
        if mode in MODES:
            data["run_file"] = path.name
            latest[mode] = data
    return latest


def _pct(value: float | None) -> str:
    return PENDING if value is None else f"{value * 100:.1f}%"


def _row(mode: str, run: dict[str, Any] | None) -> str:
    label = SYSTEM_LABELS[mode]
    if run is None:
        cost = "$0" if mode == "linter-only" else PENDING
        return f"| {label} | {PENDING} | {PENDING} | {PENDING} | {PENDING} | {cost} |"
    agg: dict[str, Any] = run["aggregate"]
    judge = run.get("judge") or {}
    quality = judge.get("comment_quality")
    quality_cell = f"{quality:.2f} (n={judge.get('n_scored')})" if quality is not None else PENDING
    cost_cell = "$0 (sin LLM)" if mode == "linter-only" else f"${agg['mean_cost_usd']:.4f}"
    return (
        f"| {label} | {_pct(agg['recall'])} | {_pct(agg['precision'])} | {quality_cell} | "
        f"{_pct(agg['fp_rate'])} | {cost_cell} |"
    )


def write_results_md(runs_dir: Path = RUNS_DIR, results_md: Path = RESULTS_MD) -> str:
    """Regenerate ``eval/RESULTS.md`` from the runs directory and return the text."""
    runs = latest_runs(runs_dir)
    lines = [
        "# Resultados de evaluación",
        "",
        "Generado por `python -m eval.run`; cada fila proviene de un archivo en `eval/runs/`.",
        "Las celdas marcadas como pendientes no tienen corrida guardada: "
        "**ningún número se inventa**.",
        "",
    ]
    any_run = next(iter(runs.values()), None)
    if any_run:
        ds = any_run["dataset"]
        lines += [
            f"**Dataset:** `{ds['path']}` — {ds['n_cases']} PRs, "
            f"{ds['n_ground_truth']} defectos plantados, "
            f"{'SINTÉTICO (escrito a mano, declarado)' if ds['synthetic'] else 'real'}.  ",
            f"**Criterio de match:** {ds['matching']}.  ",
            "**SWE-bench Lite (300 issues):** corrida pendiente (huggingface.co bloqueado "
            "en el entorno de desarrollo; `scripts/download_data.py` está listo y probado "
            "con mocks).",
            "",
        ]
    lines += [
        "## Tabla obligatoria",
        "",
        "| Sistema | Bug Recall | Bug Precision | Comment Quality | FP rate | Costo/PR |",
        "|---|---|---|---|---|---|",
    ]
    for mode in MODES:
        lines.append(_row(mode, runs.get(mode)))
    lines += [
        "",
        "Comment Quality = LLM-as-judge (rúbrica numerada en `src/eval/judge.py`: actionability, "
        "specificity, correctness; media normalizada a 0-1 sobre hasta 100 comentarios).",
        "",
    ]
    for mode in MODES:
        run = runs.get(mode)
        if run is None:
            continue
        agg = run["aggregate"]
        lines += [
            f"## Detalle — {SYSTEM_LABELS[mode]}",
            "",
            f"- Corrida: `eval/runs/{run['run_file']}` ({run['generated_at']}), "
            + (
                "sin LLM (fallback determinista)"
                if not run["llm_used"]
                else f"modelo {run['model']}"
            ),
            f"- F1: {agg['f1']:.3f} · TP comentarios: {agg['true_positives']} · "
            f"FP: {agg['false_positives']} "
            f"· GT detectados: {agg['matched_truth']}/{agg['n_ground_truth']}",
            f"- Comentarios por PR limpio: {agg['comments_per_clean_pr']:.2f}",
            f"- Latencia media: {agg['mean_latency_ms']:.0f} ms · "
            f"p95: {agg['p95_latency_ms']:.0f} ms "
            f"· costo medio/PR: ${agg['mean_cost_usd']:.4f}",
            "",
            "| Categoría | GT | Recall | Precision | Comentarios |",
            "|---|---|---|---|---|",
        ]
        for cat, m in agg["per_category"].items():
            lines.append(
                f"| {cat} | {int(m['ground_truth'])} | {m['recall'] * 100:.1f}% | "
                f"{m['precision'] * 100:.1f}% | {int(m['comments'])} |"
            )
        lines += [
            "",
            "### 10 peores casos",
            "",
            "| Caso | GT | Detectados | FP | Falló en |",
            "|---|---|---|---|---|",
        ]
        for w in run["worst_cases"]:
            missed = "; ".join(f"{m['category']}@{m['line']}" for m in w["missed"]) or "—"
            fps = "; ".join(f"{c['category']}@{c['line']}" for c in w["false_positives"][:3]) or "—"
            lines.append(
                f"| {w['case_id']} {w['title']} | {w['n_ground_truth']} | "
                f"{len(w['matched'])} | {len(w['false_positives'])} | "
                f"missed: {missed} · fp: {fps} |"
            )
        lines.append("")
    text = "\n".join(lines) + "\n"
    results_md.parent.mkdir(parents=True, exist_ok=True)
    results_md.write_text(text, encoding="utf-8")
    return text


def main(argv: list[str] | None = None) -> int:
    """CLI used by ``python -m eval.run``."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m eval.run")
    parser.add_argument("--mode", choices=MODES, default="linter-only")
    parser.add_argument("--judge", action="store_true", help="score comments with the LLM judge")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--cases", type=Path, default=SYNTHETIC_CASES)
    parser.add_argument("--name", default=None, help="run file suffix (default: mode)")
    parser.add_argument("--results-only", action="store_true", help="only regenerate RESULTS.md")
    args = parser.parse_args(argv)
    settings = Settings()
    configure_logging("WARNING")
    if args.results_only:
        write_results_md(RUNS_DIR, RESULTS_MD)
        logger.warning("RESULTS.md regenerated from eval/runs")
        return 0
    try:
        run = asyncio.run(
            run_eval(
                args.mode,
                settings,
                cases_path=args.cases,
                runs_dir=RUNS_DIR,
                results_md=RESULTS_MD,
                judge=args.judge,
                limit=args.limit,
                name=args.name,
            )
        )
    except EvalConfigError as exc:
        logger.error(str(exc))
        return 2
    agg = run["aggregate"]
    logger.warning(
        "{}: recall={:.1%} precision={:.1%} fp_rate={:.1%} cost/PR=${:.4f} -> {}",
        run["mode"],
        agg["recall"],
        agg["precision"],
        agg["fp_rate"],
        agg["mean_cost_usd"],
        run["run_file"],
    )
    return 0
