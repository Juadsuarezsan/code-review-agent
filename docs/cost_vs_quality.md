# Costo vs calidad: ¿cuándo vale la pena el LLM frente a ruff solo?

## Fórmula de costo por PR

Con `claude-sonnet-4-5-20250929` ($3 por millón de tokens de entrada, $15 por millón de
salida; tabla en `src/llm/pricing.py`), el pipeline completo hace:

```
costo_PR = Σ_archivos (in_i · 3 + out_i · 15) / 10⁶     # detect_bugs, uno por archivo con líneas añadidas
         + (in_s · 3 + out_s · 15) / 10⁶                 # synthesize_comments, uno por PR
```

Cada respuesta del API devuelve `input_tokens`, `output_tokens` y `cost_usd` reales, y
`python -m eval.run --mode pipeline` promedia ese valor en la columna *Costo/PR*.

Estimación con supuestos explícitos (4 archivos, 2.500/400 tokens por archivo,
1.800/600 en el sintetizador): **≈ $0,068 por PR**. *Claude single-pass* (un prompt
con todo el diff, ~6.000/800 tokens) ≈ $0,03 por PR. *Linter only*: $0.

## Qué se compara

| Sistema | Costo/PR | Bug Recall | Bug Precision | FP rate | Estado |
|---|---|---|---|---|---|
| Linter only (reglas + ruff + patrones) | $0 | 91,4 % | 97,0 % | 3,0 % | medido (`eval/runs/2026-09-29-linter-only.json`, set sintético) |
| Claude single-pass | ≈ $0,03 (estimado) | pendiente | pendiente | pendiente | requiere `ANTHROPIC_API_KEY` |
| Pipeline completo | ≈ $0,07 (estimado) | pendiente | pendiente | pendiente | requiere `ANTHROPIC_API_KEY` |

## Regla de decisión propuesta

Sea `R_L`, `R_P` el recall del linter y del pipeline sobre el mismo set, `P_L`, `P_P` su
precisión, y `c` el costo por PR del pipeline. El LLM vale la pena cuando el valor de los
defectos adicionales supera el costo más el tiempo humano perdido en falsos positivos:

```
(R_P − R_L) · N_defectos · V_defecto  >  c · N_PR  +  (FP_P − FP_L) · N_comentarios · T_revisión · $/hora
```

Con supuestos de industria conservadores (un bug que llega a producción cuesta ≥ 1 hora
de ingeniería, ≈ $60; un falso positivo cuesta ~2 minutos, ≈ $2), el pipeline a $0,07/PR
se paga si detecta **un defecto real adicional cada ~850 PRs** sin empeorar la precisión,
o cada ~300 PRs si además evita un falso positivo por cada 30. Los tres defectos
semánticos del eval set (8,6 % del total) son el tipo de hallazgo que el linter no puede
dar; si el pipeline los captura con precisión comparable, el umbral se cruza con holgura.

## Cuándo usar cada modo

- **Linter only** (por defecto en la Action sin llave): repositorios con muchos PRs
  pequeños, ramas de dependabot, monorepos con CI ajustado. Es determinista, auditable
  regla por regla y cuesta $0 y ~40 ms.
- **Pipeline completo**: PRs con lógica de negocio, cambios en autenticación/pagos,
  contribuciones externas. Activar por etiqueta del PR o por ruta (`src/payments/**`)
  para acotar el gasto.
- **Single-pass** solo como baseline de evaluación: sin filtro de prioridad ni
  deduplicación tiende a más ruido por el mismo costo.

## Qué falta para cerrar la tabla

1. `make eval-full` con llave: tres corridas + juez (100 comentarios) → `eval/RESULTS.md`.
2. Ablation del pipeline sin analizadores estáticos (`use_llm=True`, reglas apagadas) para
   aislar la contribución de cada capa.
3. SWE-bench Lite (300 issues reales) con `scripts/download_data.py` para un recall
   comparable con la literatura.
