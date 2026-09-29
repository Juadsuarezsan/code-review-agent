# Rendimiento

## Medición (modo linter-only, sin LLM)

Fuente: `eval/runs/2026-09-29-linter-only.json` (38 PRs sintéticos, 1–2 archivos cada
uno, ejecutado en el contenedor de desarrollo, un solo proceso).

| Métrica | Valor |
|---|---|
| Latencia media por PR | 36 ms |
| Latencia p95 por PR | 43 ms |
| Costo por PR | $0 (0 llamadas al modelo) |
| Comentarios por PR (media) | 0,87 |

Desglose por nodo (log `node exit … latency_ms`, valores típicos observados en la corrida
y en `tests/test_pipeline.py`):

| Nodo | Latencia típica | Comentario |
|---|---|---|
| `parse_diff` | < 1 ms | puro Python |
| `build_context` | 1–2 ms | tree-sitter parsea el snippet completo por archivo |
| `detect_bugs` (estático) | < 1 ms | 18 regex por línea añadida |
| `check_style` | **25–35 ms por archivo** | arranque del subproceso `ruff`; es el cuello de botella |
| `scan_security` | 1–3 ms | 23 patrones por línea |
| `review_performance` | < 1 ms | un pase con pila de ámbitos |
| `analyze_test_gaps` | < 1 ms | regex sobre el texto de tests |
| `synthesize_comments` / `prioritize` | < 1 ms | listas cortas |

**Dónde está el cuello de botella y por qué:** `ruff` se invoca una vez por archivo con
`python -m ruff`; el costo es el arranque del proceso (~25 ms), no el análisis. Los
archivos se procesan en paralelo con `asyncio.to_thread`, así que un PR de 4 archivos
sigue costando ~35 ms y no 140 ms. Para PRs de decenas de archivos la mejora obvia es
pasar todos los snippets a un único proceso (ruff acepta varios archivos), reduciendo el
overhead a uno por PR.

## Con LLM (estimación, no medición)

Con `ANTHROPIC_API_KEY`, `detect_bugs` hace una llamada por archivo con líneas añadidas y
`synthesize_comments` una llamada por PR, en paralelo por archivo. Con Sonnet 4.5 cada
llamada suele tardar 1,5–4 s, por lo que la latencia por PR estaría dominada por la
llamada más lenta más la del sintetizador (~3–8 s). El costo se calcula en tiempo real a
partir de `usage` (`src/llm/pricing.py`) y se devuelve en `cost_usd`; no hay cifra medida
porque no hay corrida con llave.

## Cache

`ReviewCache` (LRU en memoria, clave `sha256(diff) + modo + max_comments`) convierte una
re-ejecución sobre el mismo diff —el caso típico de la Action al re-lanzar un job— en
~1 ms. `GET /api/reviews/recent` expone aciertos y fallos.

## Verificaciones ejecutadas en este entorno

| Verificación | Resultado |
|---|---|
| `pytest --cov=src --cov-fail-under=70` | verde, cobertura 97,7 % (`src/`) |
| `ruff check .` · `black --check .` | sin hallazgos |
| `mypy --strict src/` | sin errores (38 archivos) |
| `gitleaks detect --no-banner --redact` (v8.21.2, binario de GitHub Releases) | 32 commits escaneados, **no leaks found**. `.gitleaks.toml` allowlista el token sintético `sk-live-…` del caso `syn-018` del eval set, plantado a propósito como CWE-798 |
| `python -m eval.run --mode linter-only` | regenera `eval/RESULTS.md` sin intervención |

Pendiente por entorno: `docker compose up` (sin demonio Docker), traces en LangSmith y
las tres corridas con modelo (sin `ANTHROPIC_API_KEY`).
