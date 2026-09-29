# Análisis de errores

Fuente: `eval/runs/2026-09-29-linter-only.json` (modo **linter-only**, fallback
determinista, sin LLM) sobre `data/eval/cases.jsonl` (38 PRs sintéticos, 35 defectos
plantados). Recall 91,4 % (32/35), precisión 97,0 % (32 TP / 33 comentarios), F1 0,941,
FP rate 3,0 %, 0 comentarios en los 5 PRs limpios. La tabla de los 10 peores casos se
regenera en `eval/RESULTS.md`; aquí se explica la causa de cada fallo.

## Los casos donde el sistema falla peor

| # | Caso | Qué pasó | Hipótesis de la causa | Qué lo arreglaría |
|---|---|---|---|---|
| 1 | `syn-010` Operador de comparación invertido (`value >= limit` en vez de `<=`) | Defecto no detectado | No hay señal sintáctica: la línea es válida y idiomática. Solo se detecta entendiendo el nombre `within` y la intención del test. | Nodo `detect_bugs` con Claude (recibe el símbolo tocado y el test que lo llama). |
| 2 | `syn-011` `return` omitido en una rama (`0.3` como expresión suelta) | No detectado | ruff tiene B018 (*useless expression*) pero se ignora a propósito porque en fragmentos de diff produce ruido; sin B018 no queda regla. | Reactivar B018 solo para literales numéricos/cadenas dentro de `if/elif`; o el pase LLM. |
| 3 | `syn-013` Variable equivocada en el bucle (`rows[0].amount` en vez de `row.amount`) | No detectado | Semántico puro; el índice constante dentro del bucle es sospechoso pero no inválido. | Heurística "índice constante sobre el iterable dentro de su propio `for`" (baja confianza) + LLM. |
| 4 | `syn-004` Off-by-one en `range(len(items) + 1)` | Detectado, pero con **1 falso positivo** (`perf:range-len`) | La regla de rendimiento "usa `enumerate`" dispara sobre la misma línea. El criterio de match exige categoría, así que cuenta como ruido. | Suprimir `perf:range-len` cuando la misma línea ya tiene un hallazgo de categoría `bug` (regla de precedencia en el sintetizador). |
| 5 | `syn-008` Archivo abierto sin `with` | Detectado (tras el ajuste) | Inicialmente ruff `SIM115` se clasificaba como `style` y duplicaba el hallazgo estático `bug`; se movió SIM115 a la categoría `bug` para que el dedupe los fusione. | Ya aplicado; se documenta porque muestra cómo la taxonomía afecta la precisión. |
| 6 | `syn-018` Token hardcodeado `api_token = 'sk-live-…'` | Detectado (tras el ajuste) | La regex original exigía `\btoken`; `api_token` no tiene frontera de palabra antes de `token`. | Ya aplicado (`\w*(…token…)\w*`). Lección: probar cada regla con identificadores compuestos. |
| 7 | `syn-012` División por `len()` tras eliminar el guard | Detectado con confianza 0,5 | La regla `division-by-len` es de severidad `info`: en un PR grande el filtro de prioridad podría dejarla fuera con `MAX_COMMENTS_PER_PR` bajo. | Subir la confianza cuando el diff **elimina** una línea `if not x` cercana (usar `removed`). |
| 8 | `syn-025` N+1 dentro del bucle | Detectado con confianza 0,65 | La heurística acierta porque `db.execute(` está en el patrón; una llamada como `repo.load(id)` no se reconocería. | Lista configurable de métodos de I/O; o el LLM con contexto de imports. |
| 9 | `syn-032` Rama nueva sin test que la referencie | Detectado con confianza 0,55 | El test añadido (`test_more`) no menciona `cost`, así que el analizador acierta; pero si el test la mencionara sin cubrir la rama nueva, no lo sabríamos. | Cobertura real (ejecutar tests) queda fuera del alcance; el LLM puede juzgar si el test cubre la rama. |
| 10 | `syn-029` `type(x) == str` + `print` | Detectados ambos | Sin error, pero la línea `print('coercing', value)` recibe dos hallazgos (regla estática + ruff) que el sintetizador fusiona por `(archivo, línea, categoría)`; si las categorías divergieran habría duplicado. | Mantener la tabla `BUG_CODES` de ruff sincronizada con la taxonomía de las reglas estáticas (test de consistencia pendiente). |

## Patrones

1. **Todo lo que falla es semántico.** Los tres fallos de recall no tienen forma
   sintáctica distintiva. Es la frontera natural del baseline y la justificación de la
   capa LLM; su contribución se medirá con `make eval-full` cuando exista llave.
2. **El único FP viene de solapamiento de categorías.** Dos reglas correctas sobre la misma
   línea con categorías distintas. La regla de precedencia (bug > performance en la misma
   línea) es la siguiente mejora de precisión.
3. **La taxonomía decide la métrica.** Dos ajustes de clasificación (SIM115, `api_token`)
   movieron el recall de 85,7 % a 91,4 % sin cambiar la lógica de detección: al evaluar
   revisores de código, la tabla de mapeo regla → categoría es tan importante como las
   reglas.

## Limitaciones del análisis

- El eval set es sintético y pequeño; los casos "limpios" fueron escritos por la misma
  persona que escribió las reglas, así que el 0 % de ruido en PRs limpios es optimista.
- No hay medición de *comment quality*: la rúbrica existe (`src/eval/judge.py`) pero
  requiere `ANTHROPIC_API_KEY`.
- SWE-bench Lite (defectos reales, sin categoría) daría una imagen muy distinta: el
  criterio documentado en `docs/data_schema.md` no exige categoría y el baseline tendría
  un recall mucho menor. Esa corrida está pendiente.
