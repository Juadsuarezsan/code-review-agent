"""``python -m eval.run [--mode linter-only|single-pass|pipeline] [--judge]``.

Thin wrapper over :func:`src.eval.runner.main` so the command in the README works
from the repository root.
"""

from __future__ import annotations

from src.eval.runner import main

if __name__ == "__main__":
    raise SystemExit(main())
