# Arquitectura

![Diagrama de arquitectura](architecture.svg)

## Flujo de una review

1. **Entrada.** `POST /api/review` recibe un diff unificado o la URL de un PR público
   (`src/api/main.py`). Con URL, `GitHubClient` (`src/github/client.py`) descarga el diff
   por la API REST con `Accept: application/vnd.github.v3.diff`, timeout de 20 s y
   reintentos exponenciales (`tenacity`) ante 429/5xx/errores de red.
2. **Validación.** Pydantic rechaza con 422 los cuerpos sin `diff` ni `pr_url`, los que
   traen ambos, las URLs que no son de PR y los diffs vacíos; el endpoint añade el límite
   de tamaño (`MAX_DIFF_BYTES`) y el chequeo de estructura (`parse_unified_diff_strict`).
3. **Cache.** La clave es `sha256(diff) + modo + max_comments`; un acierto devuelve la
   respuesta anterior con `cached=true` y un `trace_id` nuevo (`src/storage/cache.py`).
4. **Grafo LangGraph** (`src/graph/pipeline.py`):

   | Nodo | Qué hace | LLM |
   |---|---|---|
   | `parse_diff` | Diff → `FileDiff` con numeración del lado nuevo, hunks, archivos nuevos/borrados | no |
   | `build_context` | AST con tree-sitter (fallback `ast`): funciones/clases tocadas, imports, archivos relacionados en el mismo PR | no |
   | `detect_bugs` | 16 reglas estáticas + 2 reglas multilínea; con llave, pase semántico de Claude por archivo con el contexto AST | opcional |
   | `check_style` | `ruff` como subproceso sobre el snippet reconstruido con numeración real; solo líneas añadidas | no |
   | `scan_security` | `semgrep` si está configurado; si no, 23 patrones con CWE | no |
   | `review_performance` | heurísticas con seguimiento de bucles/`async def` por indentación (O(n²), N+1, `re.compile` en bucle, `time.sleep` en coroutine…) | no |
   | `analyze_test_gaps` | funciones públicas tocadas (según AST) que ningún cambio de tests referencia | no |
   | `synthesize_comments` | sanitiza, normaliza y deduplica por `(archivo, línea, categoría)`; con llave, Claude reescribe los comentarios que sobreviven al filtro | opcional |
   | `prioritize` | score = severidad + categoría + confianza; top-N global y tope por archivo | no |

   Los cinco analizadores corren en paralelo (fan-out) y confluyen en el sintetizador
   (fan-in) mediante reducers `operator.add` sobre `comments`, `errors` y `llm_results`.
   Los nombres de nodo nunca coinciden con claves del estado (LangGraph lo prohíbe).
5. **Salida.** JSON con comentarios, resumen, `trace_id`, latencia, tokens y `cost_usd`;
   opcionalmente `post_to_github=true` crea una review con comentarios inline
   (`POST /repos/{o}/{r}/pulls/{n}/reviews`, `side=RIGHT`).

## Modos de ejecución

- **linter-only** (sin `ANTHROPIC_API_KEY`): todos los nodos deterministas; costo $0. Es
  la fila "Linter only" de la tabla de evaluación y el modo de la demo estática.
- **pipeline** (con llave): además, `detect_bugs` y `synthesize_comments` llaman a
  `claude-sonnet-4-5-20250929` a través de `src/llm/client.py` (SDK oficial, timeout,
  reintentos, contabilidad de tokens y costo). Un fallo del modelo se registra en
  `errors` y la review sigue con los hallazgos deterministas.

## Separación de capas

```
config.py            variables de entorno (pydantic-settings)      ← nada lee os.environ directamente
parser/, context/    dominio puro: diff → estructuras, AST
analyzers/           dominio: reglas y adaptadores (Protocol) a ruff/semgrep/Claude
graph/               orquestación LangGraph + métricas por nodo
llm/, github/        I/O externo con timeout + tenacity (probado con mocks/respx)
storage/             cache LRU en memoria; PostgreSQL opcional (psycopg pool)
api/                 FastAPI: validación, CORS, rate limit, trace_id
eval/, src/eval/     harness reproducible: métricas, rúbrica del juez, baselines
```

## Observabilidad

- `trace_id` por request (header `X-Trace-Id` o generado), propagado con `contextvars`
  a todos los logs (`loguru`).
- Cada nodo registra entrada/salida (`node enter/exit`, número de comentarios, latencia).
- `RequestMetrics` acumula llamadas al LLM, tokens de entrada/salida y `cost_usd`; se
  devuelven en la respuesta y se persisten en PostgreSQL cuando hay `DATABASE_URL`.
- LangSmith: LangGraph emite traces automáticamente con `LANGCHAIN_TRACING_V2=true`,
  `LANGCHAIN_API_KEY` y `LANGCHAIN_PROJECT` (declaradas en `.env.example`; no ejecutado
  en este entorno por falta de llave).

## GitHub Action

`action.yml` es una acción *composite*: instala el paquete desde `github.action_path`,
ejecuta `python -m src.cli review-pr --url <PR> --post` y escribe un resumen en el
*job summary*. `.github/workflows/review-example.yml` la usa en este mismo repositorio.
