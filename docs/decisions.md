# Decisiones técnicas

Cada decisión indica el criterio concreto, la alternativa descartada y el costo de
revertirla.

## 1. LangGraph con fan-out real en vez de `asyncio.gather` a mano

- **Criterio:** los cinco analizadores son independientes y deben ejecutarse en paralelo,
  pero el sintetizador necesita *todos* sus resultados. LangGraph modela exactamente eso
  (`add_edge([...analizadores], "synthesize_comments")`) con reducers `operator.add`, y
  además emite traces a LangSmith sin código adicional.
- **Descartado:** el orquestador previo con `asyncio.gather(return_exceptions=True)`, que
  descartaba excepciones en silencio y no dejaba rastro por nodo.
- **Costo de revertir:** bajo; cada nodo es una función `async` pura sobre `ReviewState`.

## 2. SDK oficial `anthropic` con reintentos propios (`tenacity`) y modelo con fecha

- **Criterio:** el código anterior importaba `langchain_anthropic`, que no estaba en las
  dependencias (fallaba `mypy` y la instalación limpia). El SDK oficial da tipos, `usage`
  para costo por request y errores tipados (`RateLimitError`, `APIConnectionError`…). Los
  reintentos del SDK se apagan (`max_retries=0`) para que la política sea visible y
  testeable: 3 intentos, backoff exponencial 1–10 s, solo en errores transitorios.
- **Modelo:** `claude-sonnet-4-5-20250929` pinneado en `config.py` y `.env.example`; un
  alias sin fecha cambiaría el comportamiento sin cambiar el código.
- **Descartado:** LangChain `ChatAnthropic` (capa extra, sin `usage` uniforme en 0.3.x).

## 3. tree-sitter para el Context Builder, `ast` como fallback tras un `Protocol`

- **Criterio:** un diff muestra fragmentos; `ast.parse` falla ante una función cortada,
  tree-sitter produce un árbol parcial con nodos `ERROR` y sigue devolviendo las
  definiciones visibles (`tests/test_context_builder.py::test_tree_sitter_tolerates_partial_snippet`).
  Las ruedas `tree-sitter-python` pesan <1 MB, así que no comprometen el entorno.
- **Descartado:** parsear solo con regex (sin spans de función, imposible saber qué método
  toca una línea) y Python `ast` como único backend.

## 4. `ruff` como subproceso con `--isolated` sobre un snippet con numeración real

- **Criterio:** ruff no expone API Python estable; el subproceso con `--output-format json`
  y `--stdin-filename` es la interfaz soportada. El snippet se reconstruye rellenando las
  líneas invisibles con líneas vacías (`FileDiff.new_snippet`) para que `location.row`
  coincida con la línea del archivo nuevo sin cargar el repositorio completo.
- **Reglas ignoradas a propósito:** F401/F821/F811/F841/E402 (nombres definidos o usados
  fuera del hunk generan falsos positivos), E501 (ruido).
- **Descartado:** pylint (lento, API inestable) y correr ruff sobre el repo clonado
  (requiere checkout del PR; se deja como trabajo futuro).

## 5. Security Scanner con `semgrep` opcional detrás de un `Protocol`

- **Criterio:** `semgrep` arrastra >40 paquetes; no es aceptable como dependencia base de
  una Action que se instala en cada PR. `get_security_scanner(SEMGREP_BIN)` usa semgrep
  si el binario existe y, si no, el `PatternSecurityScanner` (23 reglas con CWE). Ambos se
  prueban: el de patrones con casos positivos y exclusiones, el de semgrep con el
  subproceso simulado y su JSON real.
- **Descartado:** `bandit` (cubre menos que semgrep y también es dependencia pesada).

## 6. GitHub por REST con `httpx` + `respx` en lugar de PyGithub

- **Criterio:** se usan cuatro endpoints; PyGithub es síncrono (bloquearía el event loop
  de FastAPI) y su mock es engorroso. `httpx.AsyncClient` con `respx` permite probar
  reintentos, 404 vs 5xx y el cuerpo exacto del `POST …/reviews` (`side=RIGHT`).
- **Costo:** mantener las cabeceras (`X-GitHub-Api-Version: 2022-11-28`) a mano.

## 7. Eval set sintético declarado, con ground truth derivada de marcadores

- **Criterio:** SWE-bench Lite no es alcanzable desde el entorno (huggingface.co
  bloqueado) y sus patches no son defectos anclables a una línea añadida sin el checkout
  del repo. El set sintético (38 PRs, 35 defectos, 5 PRs limpios) se genera desde
  `scripts/build_eval_set.py`: el defecto se designa por un *substring* único de la línea
  y el número se calcula, eliminando errores de conteo manual. Cada registro lleva
  `"synthetic": true`. `scripts/download_data.py` queda listo para la corrida real.
- **Descartado:** reportar métricas sobre la muestra de 20 filas previa (no tenía
  `patch`, así que no había ground truth).

## 8. Métrica de match: mismo archivo, misma categoría, ±2 líneas

- **Criterio:** los comentarios inline se anclan a la sentencia o a la línea `def`;
  una tolerancia de 2 líneas absorbe esa ambigüedad sin aceptar coincidencias
  casuales. Exigir la categoría evita que un aviso de estilo "cuente" como detección de
  un bug de seguridad en la misma línea (así el FP de `perf:range-len` en `syn-004`
  se contabiliza como ruido, honestamente).

## 9. Priority Filter por score y no solo por severidad

- **Criterio:** con solo severidad, diez `warning` de estilo desplazan un `warning` de
  seguridad. El score suma severidad (30/15/5), categoría (seguridad 8 … docs 0,5) y
  5×confianza, con tope por archivo (`MAX_COMMENTS_PER_FILE`) para que un archivo ruidoso
  no consuma el presupuesto.

## 10. Postgres opcional, cache LRU obligatoria

- **Criterio:** la Action y la demo no tienen base de datos; el historial es útil pero no
  crítico. `ReviewRepository` se activa solo con `DATABASE_URL` y se prueba con un pool
  falso; la cache por `sha256(diff)` sí es siempre útil (re-ejecuciones de la Action al
  hacer `synchronize` sobre el mismo diff).
