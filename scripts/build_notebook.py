#!/usr/bin/env python
"""Build ``notebooks/demo.ipynb`` and execute its cells so outputs are committed.

The notebook runs entirely offline (linter-only mode) and needs no API key. Cells
are executed here with ``exec`` in a shared namespace, capturing stdout, so the
committed notebook shows real outputs without requiring Jupyter at build time.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "notebooks" / "demo.ipynb"

CELLS: list[tuple[str, str]] = [
    (
        "markdown",
        "# Code Review Agent — demo end-to-end\n\n"
        "Este notebook ejecuta el pipeline completo **sin llave de API** (modo `linter-only`, "
        "fallback determinista). Con `ANTHROPIC_API_KEY` en el entorno, el mismo código activa "
        "los nodos de Claude (Bug Detector semántico y Comment Synthesizer).",
    ),
    (
        "code",
        "import asyncio, json, sys\n"
        "from pathlib import Path\n"
        "ROOT = Path.cwd() if (Path.cwd() / 'src').exists() else Path.cwd().parent\n"
        "sys.path.insert(0, str(ROOT))\n"
        "from src.config import Settings\n"
        "from src.graph.pipeline import ReviewPipeline, NODE_NAMES\n"
        "from src.observability import configure_logging\n"
        "configure_logging('WARNING')\n"
        "settings = Settings(_env_file=None)\n"
        "pipeline = ReviewPipeline(settings)\n"
        "print('mode:', pipeline.mode, '| parser:', pipeline.parser.name, "
        "'| scanner:', pipeline.scanner.name)\n"
        "print('nodes:', ', '.join(NODE_NAMES))",
    ),
    ("markdown", "## 1. Un diff con defectos plantados"),
    (
        "code",
        "DIFF = '''diff --git a/app/backup.py b/app/backup.py\n"
        "--- a/app/backup.py\n"
        "+++ b/app/backup.py\n"
        "@@ -1,5 +1,9 @@\n"
        "-import subprocess\n"
        "+import os\n"
        " \n"
        " \n"
        "-def backup(name):\n"
        "-    subprocess.run(['tar', 'czf', f'{name}.tgz', name], check=True)\n"
        "+def backup(name, seen=[]):\n"
        "+    out = ''\n"
        "+    for n in seen:\n"
        "+        out += n\n"
        "+    try:\n"
        "+        return os.system('tar czf ' + name + '.tgz ' + name)\n"
        "+    except:\n"
        "+        return None\n"
        "'''\n"
        "result = asyncio.run(pipeline.run(DIFF, max_comments=10))\n"
        "print(result.summary)\n"
        "for c in result.comments:\n"
        "    print(f'{c.file}:{c.line} [{c.severity}/{c.category}] {c.body}  <{c.rule_id}>')",
    ),
    ("markdown", "## 2. Observabilidad: latencia por nodo, tokens y costo"),
    (
        "code",
        "print(json.dumps(result.metrics.as_dict(), indent=2)[:1200])",
    ),
    ("markdown", "## 3. Contexto AST (tree-sitter) de los archivos tocados"),
    (
        "code",
        "for ctx in result.contexts:\n"
        "    print(ctx.path, '| backend:', ctx.parse_backend)\n"
        "    print('  ', ctx.summary().replace('\\n', ' | '))",
    ),
    ("markdown", "## 4. Evaluación offline reproducible (`python -m eval.run --mode linter-only`)"),
    (
        "code",
        "from src.eval.runner import latest_runs, RUNS_DIR\n"
        "runs = latest_runs(RUNS_DIR)\n"
        "for mode, run in runs.items():\n"
        "    agg = run['aggregate']\n"
        "    print(f\"{mode}: recall={agg['recall']:.1%} precision={agg['precision']:.1%} \"\n"
        "          f\"fp_rate={agg['fp_rate']:.1%} cost/PR=${agg['mean_cost_usd']:.4f} \"\n"
        "          f\"({run['dataset']['n_cases']} PRs, \"\n"
        "          f\"synthetic={run['dataset']['synthetic']})\")\n"
        "print('pending:', [m for m in ('single-pass', 'pipeline') if m not in runs], "
        "'-> requieren ANTHROPIC_API_KEY')",
    ),
]


def build() -> dict[str, object]:
    """Execute the code cells and return the notebook JSON structure."""
    namespace: dict[str, object] = {}
    cells = []
    for kind, source in CELLS:
        if kind == "markdown":
            cells.append({"cell_type": "markdown", "metadata": {}, "source": source})
            continue
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            exec(compile(source, "<cell>", "exec"), namespace)  # noqa: S102 - trusted, local cells
        cells.append(
            {
                "cell_type": "code",
                "execution_count": len(cells) + 1,
                "metadata": {},
                "source": source,
                "outputs": [{"output_type": "stream", "name": "stdout", "text": buffer.getvalue()}],
            }
        )
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.11"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def main() -> int:
    """Write the executed notebook."""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    sys.stdout.write(f"wrote {OUT}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
