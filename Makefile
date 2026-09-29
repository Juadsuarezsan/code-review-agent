.PHONY: install test lint format typecheck eval eval-full demo notebook data run gitleaks all

PY ?= .venv/bin/python
PIP ?= .venv/bin/pip

install:            ## create the venv and install the project with dev extras
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

test:               ## run the test suite with coverage (fails under 70%)
	$(PY) -m pytest

lint:               ## ruff + black --check + mypy --strict
	$(PY) -m ruff check .
	$(PY) -m black --check .
	$(PY) -m mypy --strict src/

format:             ## auto-format with black and fix ruff findings
	$(PY) -m black .
	$(PY) -m ruff check --fix .

typecheck:
	$(PY) -m mypy --strict src/

eval:               ## offline evaluation (linter-only) -> eval/runs + eval/RESULTS.md
	$(PY) -m eval.run --mode linter-only

eval-full:          ## all three systems + LLM judge (requires ANTHROPIC_API_KEY)
	$(PY) -m eval.run --mode linter-only
	$(PY) -m eval.run --mode single-pass --judge
	$(PY) -m eval.run --mode pipeline --judge

data:               ## download SWE-bench Lite and refresh data/MANIFEST.txt (needs huggingface.co)
	$(PY) scripts/download_data.py

eval-set:           ## regenerate the synthetic eval set from scripts/build_eval_set.py
	$(PY) scripts/build_eval_set.py
	$(PY) scripts/download_data.py --manifest-only

demo:               ## regenerate demo/predictions.json with the deterministic pipeline
	$(PY) scripts/build_demo_predictions.py

notebook:           ## regenerate notebooks/demo.ipynb with executed outputs
	$(PY) scripts/build_notebook.py

run:                ## start the API locally
	$(PY) -m uvicorn src.api.main:app --reload --port 8000

gitleaks:           ## scan the repository for secrets (binary from GitHub Releases)
	gitleaks detect --no-banner --redact --source .

all: lint test eval
