"""Performance Reviewer tests."""

from src.analyzers.performance import review_performance
from src.parser.diff_parser import parse_unified_diff


def _diff(body: str, path: str = "m.py") -> str:
    lines = body.strip("\n").splitlines()
    added = "\n".join("+" + line for line in lines)
    return f"--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{added}\n"


def _rules(body: str, path: str = "m.py") -> dict[str, int]:
    comments = review_performance(parse_unified_diff(_diff(body, path))[0])
    return {c.rule_id or "": c.line for c in comments}


def test_string_concat_in_loop() -> None:
    rules = _rules(
        """
def render(rows):
    out = ""
    for row in rows:
        out += f"{row}\\n"
    return out
"""
    )
    assert rules.get("perf:quadratic-str-concat") == 4


def test_list_membership_in_loop() -> None:
    rules = _rules(
        """
def dedupe(items):
    seen = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen
"""
    )
    assert rules.get("perf:list-membership-in-loop") == 4


def test_recompile_and_n_plus_one_and_nesting() -> None:
    rules = _rules(
        """
def scan(users, db):
    for user in users:
        pattern = re.compile(user.mask)
        rows = db.execute("SELECT 1 WHERE id = %s", (user.id,))
        for a in rows:
            for b in a:
                for c in b:
                    pass
"""
    )
    assert rules["perf:recompile-in-loop"] == 3
    assert rules["perf:n-plus-one"] == 4
    assert rules["perf:deep-nesting"] == 7


def test_blocking_sleep_in_async() -> None:
    rules = _rules(
        """
async def poll():
    time.sleep(1)
"""
    )
    assert rules.get("perf:blocking-sleep-in-async") == 2
    assert "perf:blocking-sleep-in-async" not in _rules("def poll():\n    time.sleep(1)\n")


def test_misc_rules() -> None:
    rules = _rules(
        """
def f(d, items, paths):
    if "k" in d.keys():
        pass
    best = sorted(items)[0]
    for i in range(len(items)):
        items = items + [i]
        import json
        fh = open(paths[i])
        frame = pd.concat([frame, df])
"""
    )
    assert {
        "perf:keys-membership",
        "perf:sorted-for-extreme",
        "perf:range-len",
        "perf:quadratic-list-concat",
        "perf:import-in-loop",
        "perf:open-in-loop",
        "perf:dataframe-append-in-loop",
    } <= set(rules)


def test_clean_code_and_non_python() -> None:
    assert _rules("def f(xs):\n    return [x * 2 for x in xs]\n") == {}
    assert _rules("for (;;) { s += x; }", path="a.js") == {}
