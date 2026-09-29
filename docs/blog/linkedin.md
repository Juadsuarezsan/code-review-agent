# Post para LinkedIn (borrador)

Publiqué **Code Review Agent**: un revisor de pull requests que primero se mide contra un
linter antes de pedirle nada a un LLM.

Qué hace: recibe un diff (o la URL de un PR), lo analiza en paralelo desde cinco ángulos
—bugs, estilo, seguridad, rendimiento y brechas de tests— y publica en GitHub los
comentarios inline más importantes, con línea exacta, sugerencia y justificación. Corre
como GitHub Action con un solo token.

Lo que me parece más útil del proyecto no es el grafo (LangGraph, tree-sitter, ruff,
semgrep opcional, Claude Sonnet 4.5 pinneado), sino la evaluación:

- Eval set sintético declarado (38 PRs, 35 defectos plantados, 5 PRs limpios), con
  ground truth verificada automáticamente.
- Tabla obligatoria regenerable con `python -m eval.run`: el baseline determinista da
  91,4 % de recall y 97,0 % de precisión con costo $0; las filas con LLM quedan marcadas
  como pendientes hasta que exista una corrida real. Ningún número inventado.
- Rúbrica LLM-as-judge numerada en código, análisis de los 10 peores casos y una
  comparativa de costo vs calidad con supuestos explícitos.

Los tres defectos que el baseline no ve son semánticos (un operador invertido, un
`return` omitido, una variable equivocada). Ese es precisamente el hueco que el LLM
tiene que justificar con su costo (~$0,07 por PR).

Repo, demo estática y documentación: https://github.com/Juadsuarezsan/code-review-agent

#AIEngineering #DeveloperTools #CodeReview #LangGraph #Python
