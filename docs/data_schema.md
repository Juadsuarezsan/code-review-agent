# Esquema de datos

## 1. Eval set sintético — `data/eval/cases.jsonl` (commiteado)

Generado por `scripts/build_eval_set.py`; **sintético y declarado como tal** en cada
registro. 38 pull requests pequeños escritos a mano (33 con defectos plantados, 5 limpios).
SHA-256 en `data/MANIFEST.txt`.

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | str | `syn-NNN`, único |
| `title` | str | descripción corta del cambio |
| `synthetic` | bool | siempre `true` |
| `focus` | str | `bug` · `security` · `performance` · `style` · `test_gap` · `clean` |
| `clean` | bool | `true` cuando no hay defectos (mide la tasa de falsos positivos) |
| `diff` | str | diff unificado con cabeceras `diff --git`, archivo fuente y (casi siempre) archivo de tests |
| `ground_truth[]` | list | defectos plantados |
| `ground_truth[].file` | str | ruta del lado nuevo |
| `ground_truth[].line` | int ≥ 1 | línea del archivo nuevo; **siempre una línea añadida** (verificado en `tests/test_eval.py::test_synthetic_cases_integrity`) |
| `ground_truth[].category` | str | `bug` · `security` · `performance` · `style` · `test_gap` · `docs` |
| `ground_truth[].note` | str | justificación breve |

Distribución de defectos: 13 bug · 9 security · 5 performance · 4 style · 3 test_gap ·
1 docs (35 en total). Cuatro casos son "semánticos" (operador invertido, `return`
faltante, variable equivocada) y no tienen señal para un linter: existen para medir la
contribución del LLM frente al baseline.

## 2. SWE-bench Lite — `data/raw/swebench_lite_test.jsonl` (no commiteado)

Fuente: <https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite>, split `test`,
300 filas, descargado por `scripts/download_data.py` vía la API `datasets-server` (sin
`datasets`/`pyarrow`). Licencia: la indicada en la *dataset card* de SWE-bench; los
repositorios originales conservan sus propias licencias. Está en `.gitignore`
(`data/raw/`); el hash SHA-256 del archivo descargado se registra en `data/MANIFEST.txt`.

| Campo | Tipo | Descripción |
|---|---|---|
| `instance_id` | str | `owner__repo-PR`, p. ej. `django__django-15814` |
| `repo` | str | `owner/repo` |
| `base_commit` | str | SHA sobre el que aplica el parche |
| `problem_statement` | str | issue original completo |
| `hints_text` | str | comentarios del issue previos al fix |
| `patch` | str | diff dorado del fix (sin tests) |
| `test_patch` | str | diff de los tests que pasan a verde |
| `created_at` | str | ISO 8601 |
| `version` | str | versión del proyecto |
| `FAIL_TO_PASS`, `PASS_TO_PASS` | str (JSON) | tests que deben pasar tras el fix |

## 3. Ground truth derivada — `data/processed/swebench_lite_ground_truth.jsonl`

Generada desde `patch` por `src/data/swebench.extract_ground_truth`:

| Campo | Tipo | Descripción |
|---|---|---|
| `instance_id`, `repo` | str | como arriba |
| `n_files` | int | archivos tocados por el fix |
| `locations[]` | list | un elemento por hunk |
| `locations[].file` | str | ruta del lado nuevo |
| `locations[].new_start` | int | primera línea nueva del hunk |
| `locations[].new_end` | int | última línea nueva del hunk (inclusive) |

**Criterio de match para SWE-bench Lite (documentado, aún no ejecutado):** un comentario
acierta si apunta a un `file` del patch y su `line` cae en `[new_start, new_end]` de algún
hunk. No se exige categoría porque los fixes reales no vienen etiquetados.

## 4. Corridas de evaluación — `eval/runs/<fecha>-<modo>.json`

| Campo | Descripción |
|---|---|
| `mode` / `system` / `llm_used` / `model` | qué sistema se evaluó |
| `dataset` | ruta, `n_cases`, `synthetic`, `n_ground_truth`, criterio de match |
| `aggregate` | recall, precision, f1, fp_rate, comentarios por PR limpio, latencia media y p95, costo medio, desglose por categoría |
| `judge` | `null` o `{n_scored, comment_quality, judge_cost_usd, scores[]}` |
| `worst_cases[]`, `cases[]` | detalle por PR: detectados, omitidos, FP, latencia, costo, errores |

## 5. Splits y semillas

No hay entrenamiento: el eval set es únicamente de evaluación. La generación es
determinista (sin aleatoriedad) y los tests fijan `random.seed(20260516)` en
`tests/conftest.py`.
