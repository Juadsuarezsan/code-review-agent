# Escalabilidad

## Dónde está el trabajo

Una review en modo `linter-only` tarda ~40 ms (p95 medido en `eval/RESULTS.md`), casi
todo en el subproceso de `ruff` (~30 ms por archivo) que corre en `asyncio.to_thread`. En
modo `pipeline` el costo lo dominan las llamadas a Claude: una por archivo con líneas
añadidas (`detect_bugs`) más una por PR (`synthesize_comments`); cada una son 1–3 s y
~1.500–4.000 tokens de entrada.

## 100× más PRs (de ~10 a ~1.000 reviews/hora)

1. **Sin estado en el proceso.** El `ReviewPipeline` no guarda nada entre requests salvo
   `RequestMetrics`; N workers de `uvicorn` detrás de un balanceador escalan linealmente.
   La cache LRU pasa a Redis (misma clave `sha256(diff)`), lo que además comparte
   aciertos entre workers.
2. **Cola para la Action.** `POST /api/review` con `pr_url` se convierte en *enqueue*; un
   worker consume la cola, ejecuta el grafo y postea la review. GitHub tolera reviews
   asíncronas; el usuario no espera la respuesta HTTP.
3. **Concurrencia por archivo ya existe.** Los cinco analizadores corren en paralelo y
   `detect_bugs` lanza una llamada al modelo por archivo con `asyncio.gather`; el límite
   práctico es el *rate limit* de tokens por minuto de la cuenta. Un semáforo global
   (`asyncio.Semaphore`) por proceso evita 429 en ráfagas.
4. **Prompt caching.** El system prompt de `detect_bugs` (~700 tokens) es estable y va
   primero; con `cache_control` en el bloque de sistema el costo de entrada cae ~90 % en
   esa parte. No se activó porque no hay corrida real que lo mida.

## 100× más archivos por PR

- El diff se acota con `MAX_DIFF_BYTES` (400 KB por defecto → 422). Para monorepos se
  fragmenta por archivo y se revisa por lotes de ~20 archivos por invocación del grafo,
  manteniendo el filtro de prioridad global al final.
- `ruff` y `semgrep` aceptan varios archivos por proceso: un solo subproceso por lote
  reduce el overhead de arranque (~25 ms) a uno por PR.

## Costo con 1.000 usuarios mensuales (supuestos explícitos)

| Supuesto | Valor |
|---|---|
| PRs por usuario y mes | 20 |
| Archivos con cambios por PR | 4 |
| Tokens de entrada por llamada (`detect_bugs`) | 2.500 |
| Tokens de salida por llamada | 400 |
| Llamada extra `synthesize_comments` | 1.800 in / 600 out |
| Precio `claude-sonnet-4-5-20250929` | $3 / M entrada · $15 / M salida |

Costo por PR ≈ 4 × (2.500×3 + 400×15)/10⁶ + (1.800×3 + 600×15)/10⁶ ≈ **$0,068**.
Con 20.000 PRs/mes → **≈ $1.360/mes en modelo**, más ~$50–100 de cómputo (2 réplicas
pequeñas + Redis). En modo `linter-only` el costo de modelo es $0. Estos son supuestos:
el número real de tokens se mide con `cost_usd` en cada respuesta y se reporta en
`eval/RESULTS.md` cuando exista una corrida con llave.

## Cuellos de botella conocidos

- El grafo mantiene `_current_metrics` por instancia; la API crea la instancia una vez y
  la usa por request de forma secuencial en cada worker. Para concurrencia intra-proceso
  alta se instancia un pipeline por request (barato: los analizadores no tienen estado).
- `tree-sitter` parsea el snippet completo por archivo; para archivos de >10 k líneas
  conviene parsear solo las ventanas de cada hunk.
