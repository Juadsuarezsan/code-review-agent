# Un agente de code review que primero se mide contra ruff

*Borrador de artículo técnico (≈1.800 palabras). Autor: Juan David Suárez Sánchez.*

## Por qué otro revisor automático

Cada equipo que he visto crecer más allá de diez personas termina con la misma cola: los
pull requests esperan revisión, los revisores revisan cansados y los mismos tres errores
(una credencial en el código, un `except:` que se traga todo, una consulta dentro de un
bucle) vuelven a entrar una y otra vez. Herramientas como Copilot code review, CodeRabbit o
Codacy venden exactamente esto, y su promesa es razonable. Lo que no suelen mostrar es la
pregunta incómoda: **¿cuánto de lo que encuentran lo habría encontrado un linter de $0?**

Construí *Code Review Agent* con esa pregunta como diseño. El sistema tiene dos capas: una
determinista (reglas estáticas, `ruff`, un scanner de seguridad con CWE, heurísticas de
rendimiento y un analizador de brechas de tests) y una capa LLM opcional con Claude. Las
dos capas comparten el mismo grafo, la misma evaluación y la misma tabla de resultados.
El baseline no es un adorno: es la fila que obliga a la capa LLM a justificar su costo.

## Qué hace el sistema

Recibe un diff unificado —o la URL de un PR público de GitHub— y devuelve una lista corta
de comentarios anclados a líneas añadidas: archivo, línea, severidad, categoría, texto,
sugerencia de código y justificación. Los comentarios se pueden publicar directamente
como una review inline en GitHub (`POST /repos/{owner}/{repo}/pulls/{n}/reviews`, con
`side=RIGHT`), y una GitHub Action *composite* lo hace en cada PR sin más configuración
que un token.

La orquestación es un grafo de LangGraph con nueve nodos:

```
parse_diff → build_context → { detect_bugs, check_style, scan_security,
                               review_performance, analyze_test_gaps }
           → synthesize_comments → prioritize
```

Los cinco analizadores corren en paralelo (fan-out) y sus listas de comentarios se
concatenan con un reducer `operator.add`; el sintetizador (fan-in) las limpia y deduplica
y el filtro de prioridad elige las N mejores. Cada nodo escribe en el log su entrada, su
salida y su latencia con un `trace_id` por request, y el cliente del modelo contabiliza
tokens y costo en dólares por llamada.

## Tres decisiones que importaron más de lo que esperaba

### 1. Numeración de líneas: el detalle que decide si un comentario sirve

Un comentario inline es inútil si apunta a la línea equivocada. El parser de diffs
mantiene la numeración del **lado nuevo** y, además, reconstruye un *snippet* del archivo
nuevo rellenando con líneas vacías todo lo que el diff no muestra. Ese truco tan simple
permite pasar el snippet a `ruff` (como subproceso, con `--stdin-filename`) y a
tree-sitter, y que los números de línea que devuelven coincidan con los del archivo real
sin haber clonado el repositorio. Solo se reportan hallazgos sobre líneas añadidas: los
problemas preexistentes no son culpa del PR.

Hubo que cerrar los hunks contando las longitudes declaradas en la cabecera `@@`. Sin
eso, un diff "plano" (sin `diff --git`) que concatena dos archivos hacía que el `---
a/tests/test_x.py` del segundo archivo se leyera como una línea eliminada. Ese bug lo
encontró un test, no un usuario, y es el tipo de cosa que explica por qué el parser tiene
más tests que cualquier otro módulo.

### 2. tree-sitter porque un diff son fragmentos

El Context Builder necesita saber qué función toca cada línea añadida, para que el LLM
reciba "estás en `Repo.save`, que importa `helper` de `app.utils`" en lugar de un pedazo
de texto suelto, y para que el analizador de brechas de tests sepa que se modificó el
cuerpo de una función pública aunque no aparezca ningún `def` en el diff. `ast.parse`
falla ante una función cortada; tree-sitter produce un árbol con nodos `ERROR` y sigue
entregando las definiciones visibles. Ambos backends implementan el mismo `Protocol`,
y el de la biblioteca estándar queda como fallback.

### 3. Dependencias pesadas detrás de un `Protocol`

`semgrep` es una gran herramienta y un pésimo requisito para una Action que se instala
en cada PR. El scanner de seguridad tiene dos implementaciones intercambiables: la de
semgrep (subproceso, JSON, usada solo si `SEMGREP_BIN` apunta a un binario) y una de 23
patrones anotados con CWE (`os.system`, `pickle.loads`, SQL con f-strings, credenciales
hardcodeadas, `verify=False`, `random` para tokens, `tempfile.mktemp`, `requests.get`
sin `timeout`…). La de patrones cubre el 100 % de los casos de seguridad del eval set;
la de semgrep se prueba con su JSON real y el subproceso simulado.

## Evaluar sin inventar

Aquí está la parte que más tiempo consumió y la que más vale. El plan original era medir
sobre SWE-bench Lite (300 issues reales con su parche dorado). El entorno de desarrollo
no puede alcanzar huggingface.co, así que el script de descarga existe, está probado con
HTTP simulado y extrae la ground truth a nivel de hunk del `patch`, pero no se ha
ejecutado. Publicar métricas "de SWE-bench" en esa situación sería mentir.

La alternativa fue un eval set sintético, **declarado como tal en cada registro**: 38
pull requests pequeños escritos a mano —33 con defectos plantados y 5 limpios— generados
desde un script donde cada defecto se designa por un *substring* único de la línea
ofensiva. El número de línea se calcula, no se escribe, y un test verifica que cada
defecto está en una línea añadida (si no, no habría forma de comentarlo inline). El set
incluye cuatro casos "semánticos" —un operador de comparación invertido, un `return`
omitido, una variable equivocada dentro de un bucle— que no tienen ninguna señal
sintáctica. Existen para medir la contribución del LLM.

El criterio de acierto es explícito: mismo archivo, misma categoría, distancia de línea
≤ 2. Exigir la categoría fue una decisión consciente: sin ella, un aviso de rendimiento
sobre `range(len(items) + 1)` habría "detectado" el bug de índice de esa misma línea.
Con el criterio estricto, ese aviso cuenta como falso positivo, que es lo honesto.

Resultado del baseline determinista, reproducible con `python -m eval.run`:

| Sistema | Bug Recall | Bug Precision | FP rate | Costo/PR |
|---|---|---|---|---|
| Linter only (reglas + ruff + patrones, sin LLM) | 91,4 % | 97,0 % | 3,0 % | $0 |

Las filas "Claude single-pass" y "Pipeline completo" están en la tabla con la marca
*pendiente (requiere ANTHROPIC_API_KEY)*, junto con la columna de calidad de comentarios,
que se mide con un juez LLM cuya rúbrica —actionability, specificity, correctness, con
ejemplos de 1 y de 5— vive en el código y no en un prompt improvisado.

Los tres defectos que el baseline no ve son exactamente los semánticos. Los dos falsos
positivos son defendibles pero se cuentan como ruido. Ese es el punto de partida contra
el que la capa LLM tendrá que demostrar que vale su costo estimado (≈ $0,07 por PR de
cuatro archivos con Sonnet 4.5, con supuestos de tokens documentados).

## Lo que no está resuelto

- **Sin llave no hay semántica.** El baseline no entiende intención; solo forma.
- **Solo Python en profundidad.** Las gramáticas de tree-sitter y las heurísticas cubren
  Python; JavaScript recibe reglas triviales.
- **Contexto limitado al diff.** Sin clonar el repositorio no se sabe quién importa a
  quién ni cuál es el issue enlazado. Por eso se ignoran las reglas de "nombre no
  definido": sin el archivo completo son ruido garantizado.
- **38 PRs no son un benchmark.** Son una cota superior optimista para el baseline y una
  base para el análisis de errores, no una cifra comparable con la literatura.

## Cómo usarlo en cinco minutos

```bash
git clone https://github.com/Juadsuarezsan/code-review-agent && cd code-review-agent
make install && make test lint && make eval
git diff main | .venv/bin/python -m src.cli review --diff-file - --format markdown
```

Y en un repositorio propio, como Action:

```yaml
- uses: Juadsuarezsan/code-review-agent@main
  with:
    github-token: ${{ secrets.GITHUB_TOKEN }}
    anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}   # opcional
```

Sin la llave, la Action publica en cada PR los hallazgos deterministas: gratis, en menos
de un segundo, y con la misma calidad que muestra la tabla. Con la llave, los nodos de
Claude se activan y el costo por PR aparece en la respuesta y en el resumen del job.

## Qué aprendí

Que la infraestructura de evaluación es el producto. El grafo, los analizadores y la
integración con GitHub son trabajo de ingeniería previsible; lo que convierte el
proyecto en algo defendible en una entrevista —o en una decisión de compra— es poder
regenerar la tabla con un comando, explicar cada celda pendiente y señalar en el análisis
de errores por qué falla cada caso. Un revisor de código que no se mide contra `ruff` no
sabe cuánto vale.

*Código, evaluación y documentación: <https://github.com/Juadsuarezsan/code-review-agent>.*
