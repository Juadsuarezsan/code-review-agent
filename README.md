# Project 06 — Code Review Agent

> Analyzes pull requests, detects real bugs, suggests refactorizations, writes missing tests, comments inline on the diff. The space behind Copilot Workspace, Cursor, Codium, Sourcegraph Cody.

[![Status](https://img.shields.io/badge/status-planned-fbbf24)]()
[![LLM](https://img.shields.io/badge/LLM-Claude%20Sonnet%204.5-7c5cff)]()
[![Bench](https://img.shields.io/badge/bench-SWE--bench%20Lite-22d3ee)]()

**Industrial use case:** Developer tools — Cursor, Codium, Sourcegraph Cody, Codacy.

## What this project does

Reads a PR (diff + context), runs multiple analyzers in parallel (bug detector, style checker, test gap analyzer, security scanner, performance reviewer), synthesizes the top-N most actionable comments by severity. Posts inline comments to GitHub via Actions.

## Architecture

```
PR (GitHub webhook or manual input)
   │
   ▼
[PR Analyzer] PyGithub → changed files, lines, context
   │
   ▼
[Context Builder]
   ├─ AST via tree-sitter
   ├─ related files (imports, importers)
   ├─ git history
   └─ linked issue
   │
   ▼
[Multi-aspect Analyzers] (parallel)
   ├─ Bug Detector (Claude + static analysis)
   ├─ Style Checker (ruff/eslint + Claude)
   ├─ Test Gap Analyzer (Claude)
   ├─ Security Scanner (semgrep + Claude)
   └─ Performance Reviewer (Claude)
   │
   ▼
[Comment Synthesizer] Claude → inline comments
   │ location, severity, suggestion, justification
   │
   ▼
[Priority Filter] → top N by severity
   │
   ▼
[Output] post to GitHub or return JSON
```

## Roadmap to v1.0.0

1. [ ] PyGithub integration for PR reading + commenting
2. [ ] tree-sitter for multi-language AST (Python, JS/TS, Go)
3. [ ] Static analysis wrappers (ruff, mypy, eslint, semgrep)
4. [ ] LangGraph parallel analyzer pipeline
5. [ ] Comment synthesizer with severity scoring
6. [ ] Eval on SWE-bench Lite (300 issues) — bug detection F1
7. [ ] LLM-as-judge on 100 generated comments (actionability, specificity, correctness)
8. [ ] Next.js demo with diff viewer (react-diff-viewer)
9. [ ] Gallery of 10 reviews on famous public PRs
10. [ ] GitHub Action published to marketplace for self-hosted use

## Stack

| Layer | Technology |
|---|---|
| LLM | Claude Sonnet 4.5 |
| AST | tree-sitter (Python, JS/TS, Go bindings) |
| Static analysis | pylint, ruff, mypy (Python), ESLint (JS/TS) |
| Security | semgrep |
| Diff parsing | unidiff or difflib |
| GitHub | PyGithub |
| Orchestration | LangGraph |
| Storage | PostgreSQL for historical reviews |
| Frontend | Next.js with react-diff-viewer |
| Observability | LangSmith |

## Definition of Done — project-specific

- [ ] Pipeline runs over all 300 SWE-bench Lite issues
- [ ] Bug detection F1 reported against ground truth fixes
- [ ] Comment quality LLM-judged over 100 cases
- [ ] Demo: paste a diff or PR URL, see review generated
- [ ] Gallery of 10 reviews on famous public PRs in the demo
- [ ] GitHub Action published to the marketplace for use in real repos
- [ ] Cost-vs-quality analysis: when is this worth it vs ruff alone

Plus the 12 universal DoD blocks.

## License

MIT.
