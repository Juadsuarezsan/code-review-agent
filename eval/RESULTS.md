# Resultados de evaluación

Generado por `python -m eval.run`; cada fila proviene de un archivo en `eval/runs/`.
Las celdas marcadas como pendientes no tienen corrida guardada: **ningún número se inventa**.

**Dataset:** `data/eval/cases.jsonl` — 38 PRs, 35 defectos plantados, SINTÉTICO (escrito a mano, declarado).  
**Criterio de match:** same file, same category, |line - gt_line| <= 2.  
**SWE-bench Lite (300 issues):** corrida pendiente (huggingface.co bloqueado en el entorno de desarrollo; `scripts/download_data.py` está listo y probado con mocks).

## Tabla obligatoria

| Sistema | Bug Recall | Bug Precision | Comment Quality | FP rate | Costo/PR |
|---|---|---|---|---|---|
| Linter only (reglas estáticas + ruff + patrones; fallback determinista, sin LLM) | 91.4% | 97.0% | pendiente (requiere ANTHROPIC_API_KEY) | 3.0% | $0 (sin LLM) |
| Claude single-pass | pendiente (requiere ANTHROPIC_API_KEY) | pendiente (requiere ANTHROPIC_API_KEY) | pendiente (requiere ANTHROPIC_API_KEY) | pendiente (requiere ANTHROPIC_API_KEY) | pendiente (requiere ANTHROPIC_API_KEY) |
| Pipeline completo (este) | pendiente (requiere ANTHROPIC_API_KEY) | pendiente (requiere ANTHROPIC_API_KEY) | pendiente (requiere ANTHROPIC_API_KEY) | pendiente (requiere ANTHROPIC_API_KEY) | pendiente (requiere ANTHROPIC_API_KEY) |

Comment Quality = LLM-as-judge (rúbrica numerada en `src/eval/judge.py`: actionability, specificity, correctness; media normalizada a 0-1 sobre hasta 100 comentarios).

## Detalle — Linter only (reglas estáticas + ruff + patrones; fallback determinista, sin LLM)

- Corrida: `eval/runs/2026-09-29-linter-only.json` (2026-09-29T01:30:38+00:00), sin LLM (fallback determinista)
- F1: 0.941 · TP comentarios: 32 · FP: 1 · GT detectados: 32/35
- Comentarios por PR limpio: 0.00
- Latencia media: 36 ms · p95: 43 ms · costo medio/PR: $0.0000

| Categoría | GT | Recall | Precision | Comentarios |
|---|---|---|---|---|
| bug | 13 | 76.9% | 100.0% | 10 |
| docs | 1 | 100.0% | 100.0% | 1 |
| performance | 5 | 100.0% | 83.3% | 6 |
| security | 9 | 100.0% | 100.0% | 9 |
| style | 4 | 100.0% | 100.0% | 4 |
| test_gap | 3 | 100.0% | 100.0% | 3 |

### 10 peores casos

| Caso | GT | Detectados | FP | Falló en |
|---|---|---|---|---|
| syn-010 Wrong comparison operator (semantic, no linter signal) | 1 | 0 | 0 | missed: bug@2 · fp: — |
| syn-011 Missing return in branch (semantic) | 1 | 0 | 0 | missed: bug@5 · fp: — |
| syn-013 Wrong variable used in loop body (semantic) | 1 | 0 | 0 | missed: bug@4 · fp: — |
| syn-004 Off-by-one in index loop | 1 | 1 | 1 | missed: — · fp: performance@3 |
| syn-001 Config loader swallows every exception | 1 | 1 | 0 | missed: — · fp: — |
| syn-002 Mutable default accumulates across calls | 1 | 1 | 0 | missed: — · fp: — |
| syn-003 sort() result assigned | 1 | 1 | 0 | missed: — · fp: — |
| syn-005 Identity comparison with literal | 1 | 1 | 0 | missed: — · fp: — |
| syn-006 Exception handler with pass | 1 | 1 | 0 | missed: — · fp: — |
| syn-007 List mutated while iterating | 1 | 1 | 0 | missed: — · fp: — |

