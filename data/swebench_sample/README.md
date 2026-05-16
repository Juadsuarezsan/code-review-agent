# SWE-bench Lite sample

20 issues sampled from the 300 in `princeton-nlp/SWE-bench_Lite`. Each row:

```json
{
  "instance_id": "django__django-15814",
  "repo": "django/django",
  "problem_statement": "QuerySet.only() after select_related() crashes...",
  "patch_lines": 14,
  "test_patch_lines": 29
}
```

## Reproducing the full set

```python
from datasets import load_dataset
ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
# 300 real GitHub issues with golden patches + tests
```

The full SWE-bench Lite test set is what the project's eval harness runs
against for bug-detection recall/precision. The 20 here are a manageable
subset for local dev + CI.
