# Code Review Agent API image.
# NOTE: built and validated only in CI/other machines; the development environment for
# this repository has no Docker daemon, so `docker compose up` is documented as pending.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install the package first so the dependency layer is cached independently of the code.
COPY pyproject.toml README.md ./
COPY src ./src
COPY eval ./eval
RUN pip install --upgrade pip && pip install .

# Data and evaluation artefacts needed at runtime (demo cases, committed eval runs).
COPY data ./data
COPY scripts ./scripts

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
