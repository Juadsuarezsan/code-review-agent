#!/usr/bin/env python
"""Generate the synthetic evaluation set ``data/eval/cases.jsonl``.

Every case is a small, hand-written pull request: a Python module *before* and
*after* the change (often with a matching test file), plus the planted defects.
Each defect is designated by a unique substring of the offending line, so the
line number in the ground truth is computed from the text rather than typed by
hand. The diffs are produced with :mod:`difflib` in ``git diff`` format.

The set is SYNTHETIC and declared as such in every record (``"synthetic": true``).
It exists because the real benchmark (SWE-bench Lite on huggingface.co) is not
reachable from the development environment; see ``docs/data_schema.md``.

Run: ``python scripts/build_eval_set.py`` (deterministic; safe to re-run).
"""

from __future__ import annotations

import difflib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval" / "cases.jsonl"


@dataclass
class Case:
    """One synthetic pull request with planted defects."""

    id: str
    title: str
    path: str
    before: str
    after: str
    bugs: list[tuple[str, str, str]] = field(default_factory=list)  # (marker, category, note)
    test_path: str | None = None
    test_before: str = ""
    test_after: str = ""
    focus: str = "bug"

    def build(self) -> dict[str, object]:
        """Render the git-style diff and resolve ground-truth line numbers."""
        after_lines = self.after.splitlines()
        ground_truth = []
        for marker, category, note in self.bugs:
            hits = [i + 1 for i, line in enumerate(after_lines) if marker in line]
            if len(hits) != 1:
                raise SystemExit(f"{self.id}: marker {marker!r} matched {len(hits)} lines")
            ground_truth.append(
                {"file": self.path, "line": hits[0], "category": category, "note": note}
            )
        diff = _git_diff(self.path, self.before, self.after)
        if self.test_path:
            diff += _git_diff(self.test_path, self.test_before, self.test_after)
        return {
            "id": self.id,
            "title": self.title,
            "synthetic": True,
            "focus": self.focus,
            "clean": not self.bugs,
            "diff": diff,
            "ground_truth": ground_truth,
        }


def _git_diff(path: str, before: str, after: str) -> str:
    a = before.splitlines()
    b = after.splitlines()
    body = "\n".join(
        difflib.unified_diff(a, b, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=3)
    )
    header = f"diff --git a/{path} b/{path}\n"
    if not before:
        header += "new file mode 100644\n"
        body = body.replace(f"--- a/{path}", "--- /dev/null", 1)
    return header + body + "\n"


def _test(name: str, fn: str, call: str = "") -> tuple[str, str]:
    before = f"from app.{name} import {fn}\n\n\ndef test_smoke():\n    assert {fn}\n"
    after = before + f"\n\ndef test_{fn}_change():\n    {call or f'assert {fn}'}\n"
    return before, after


CASES: list[Case] = []


def add(case: Case) -> None:
    CASES.append(case)


# --------------------------------------------------------------------------- bugs
add(
    Case(
        "syn-001",
        "Config loader swallows every exception",
        "app/config.py",
        "import json\n\n\ndef load(path):\n    with open(path) as fh:\n        return json.load(fh)\n",
        "import json\n\n\ndef load(path):\n    try:\n        with open(path) as fh:\n            return json.load(fh)\n    except:\n        return {}\n",
        [("except:", "bug", "bare except hides missing file and JSON errors")],
        "tests/test_config.py",
        *_test("config", "load", "assert load('missing.json') == {}"),
    )
)
add(
    Case(
        "syn-002",
        "Mutable default accumulates across calls",
        "app/registry.py",
        "def register(name, registry):\n    registry.append(name)\n    return registry\n",
        "def register(name, registry=[]):\n    registry.append(name)\n    return registry\n",
        [("registry=[]", "bug", "shared default list")],
        "tests/test_registry.py",
        *_test("registry", "register", "assert register('a') == ['a']"),
    )
)
add(
    Case(
        "syn-003",
        "sort() result assigned",
        "app/ranking.py",
        "def top(scores):\n    ordered = sorted(scores)\n    return ordered[-1]\n",
        "def top(scores):\n    ordered = scores.sort()\n    return ordered[-1]\n",
        [("ordered = scores.sort()", "bug", "list.sort returns None")],
        "tests/test_ranking.py",
        *_test("ranking", "top", "assert top([1, 3, 2]) == 3"),
    )
)
add(
    Case(
        "syn-004",
        "Off-by-one in index loop",
        "app/windows.py",
        "def pairs(items):\n    return [(items[i], items[i + 1]) for i in range(len(items) - 1)]\n",
        "def pairs(items):\n    out = []\n    for i in range(len(items) + 1):\n        out.append((items[i], items[i + 1]))\n    return out\n",
        [("range(len(items) + 1)", "bug", "IndexError on last iterations")],
        "tests/test_windows.py",
        *_test("windows", "pairs", "assert pairs([1, 2, 3]) == [(1, 2), (2, 3)]"),
    )
)
add(
    Case(
        "syn-005",
        "Identity comparison with literal",
        "app/status.py",
        "def is_done(state):\n    return state == 'done'\n",
        "def is_done(state):\n    return state is 'done'\n",
        [("state is 'done'", "bug", "is compares identity")],
        "tests/test_status.py",
        *_test("status", "is_done", "assert is_done('done')"),
    )
)
add(
    Case(
        "syn-006",
        "Exception handler with pass",
        "app/cache.py",
        "def get(cache, key):\n    return cache[key]\n",
        "def get(cache, key):\n    try:\n        return cache[key]\n    except KeyError:\n        pass\n",
        [("except KeyError:", "bug", "silently returns None")],
        "tests/test_cache.py",
        *_test("cache", "get", "assert get({'a': 1}, 'a') == 1"),
    )
)
add(
    Case(
        "syn-007",
        "List mutated while iterating",
        "app/filters.py",
        "def drop_empty(items):\n    return [i for i in items if i]\n",
        "def drop_empty(items):\n    for item in items:\n        if not item:\n            items.remove(item)\n    return items\n",
        [("items.remove(item)", "bug", "skips elements after removal")],
        "tests/test_filters.py",
        *_test("filters", "drop_empty", "assert drop_empty([1, 0, 0, 2]) == [1, 2]"),
    )
)
add(
    Case(
        "syn-008",
        "Resource leak: file never closed",
        "app/reader.py",
        "def read_all(path):\n    with open(path) as fh:\n        return fh.read()\n",
        "def read_all(path):\n    fh = open(path)\n    data = fh.read()\n    return data\n",
        [("fh = open(path)", "bug", "handle not closed")],
        "tests/test_reader.py",
        *_test("reader", "read_all", "assert read_all(__file__)"),
    )
)
add(
    Case(
        "syn-009",
        "Validation via assert in production code",
        "app/orders.py",
        "def place(order):\n    if order.qty <= 0:\n        raise ValueError('qty')\n    return order.qty * order.price\n",
        "def place(order):\n    assert order.qty > 0, 'qty'\n    return order.qty * order.price\n",
        [("assert order.qty > 0", "bug", "stripped under -O")],
        "tests/test_orders.py",
        *_test("orders", "place"),
    )
)
add(
    Case(
        "syn-010",
        "Wrong comparison operator (semantic, no linter signal)",
        "app/limits.py",
        "def within(value, limit):\n    return value <= limit\n",
        "def within(value, limit):\n    return value >= limit\n",
        [("return value >= limit", "bug", "inverted condition")],
        "tests/test_limits.py",
        *_test("limits", "within", "assert within(1, 5)"),
    )
)
add(
    Case(
        "syn-011",
        "Missing return in branch (semantic)",
        "app/discount.py",
        "def rate(kind):\n    if kind == 'vip':\n        return 0.2\n    return 0.0\n",
        "def rate(kind):\n    if kind == 'vip':\n        return 0.2\n    elif kind == 'staff':\n        0.3\n    return 0.0\n",
        [("        0.3", "bug", "value computed but not returned")],
        "tests/test_discount.py",
        *_test("discount", "rate", "assert rate('vip') == 0.2"),
    )
)
add(
    Case(
        "syn-012",
        "Average divides by len without guard",
        "app/stats.py",
        "def mean(xs):\n    if not xs:\n        return 0.0\n    return sum(xs) / len(xs)\n",
        "def mean(xs):\n    return round(sum(xs) / len(xs), 2)\n",
        [("sum(xs) / len(xs)", "bug", "guard removed: ZeroDivisionError on empty list")],
        "tests/test_stats.py",
        *_test("stats", "mean", "assert mean([2, 4]) == 3"),
    )
)
add(
    Case(
        "syn-013",
        "Wrong variable used in loop body (semantic)",
        "app/totals.py",
        "def total(rows):\n    acc = 0\n    for row in rows:\n        acc += row.amount\n    return acc\n",
        "def total(rows):\n    acc = 0\n    for row in rows:\n        acc += rows[0].amount\n    return acc\n",
        [("acc += rows[0].amount", "bug", "always adds the first row")],
        "tests/test_totals.py",
        *_test("totals", "total"),
    )
)

# ----------------------------------------------------------------------- security
add(
    Case(
        "syn-014",
        "Shell command built from user input",
        "app/backup.py",
        "import subprocess\n\n\ndef backup(name):\n    subprocess.run(['tar', 'czf', f'{name}.tgz', name], check=True)\n",
        "import os\n\n\ndef backup(name):\n    os.system('tar czf ' + name + '.tgz ' + name)\n",
        [("os.system(", "security", "CWE-78 command injection")],
        "tests/test_backup.py",
        *_test("backup", "backup"),
        focus="security",
    )
)
add(
    Case(
        "syn-015",
        "eval on request payload",
        "app/calc.py",
        "import ast\n\n\ndef compute(expr):\n    return ast.literal_eval(expr)\n",
        "def compute(expr):\n    return eval(expr)\n",
        [("return eval(expr)", "security", "CWE-95")],
        "tests/test_calc.py",
        *_test("calc", "compute", "assert compute('1+1') == 2"),
        focus="security",
    )
)
add(
    Case(
        "syn-016",
        "pickle from network bytes",
        "app/session.py",
        "import json\n\n\ndef restore(blob):\n    return json.loads(blob)\n",
        "import pickle\n\n\ndef restore(blob):\n    return pickle.loads(blob)\n",
        [("pickle.loads(blob)", "security", "CWE-502")],
        "tests/test_session.py",
        *_test("session", "restore"),
        focus="security",
    )
)
add(
    Case(
        "syn-017",
        "SQL built with f-string",
        "app/users.py",
        "def find(cur, email):\n    cur.execute('SELECT id FROM users WHERE email = %s', (email,))\n    return cur.fetchone()\n",
        "def find(cur, email):\n    cur.execute(f\"SELECT id FROM users WHERE email = '{email}'\")\n    return cur.fetchone()\n",
        [('cur.execute(f"SELECT', "security", "CWE-89")],
        "tests/test_users.py",
        *_test("users", "find"),
        focus="security",
    )
)
add(
    Case(
        "syn-018",
        "Hard-coded API token",
        "app/client.py",
        "import os\n\nTOKEN = os.environ.get('API_TOKEN')\n\n\ndef headers():\n    return {'Authorization': f'Bearer {TOKEN}'}\n",
        "api_token = 'sk-live-9f8e7d6c5b4a3f2e1d'\n\n\ndef headers():\n    return {'Authorization': f'Bearer {api_token}'}\n",
        [("api_token = 'sk-live", "security", "CWE-798")],
        "tests/test_client.py",
        *_test("client", "headers"),
        focus="security",
    )
)
add(
    Case(
        "syn-019",
        "TLS verification disabled and no timeout",
        "app/fetch.py",
        "import requests\n\n\ndef fetch(url):\n    return requests.get(url, timeout=10).json()\n",
        "import requests\n\n\ndef fetch(url):\n    return requests.get(url, verify=False).json()\n",
        [("verify=False", "security", "CWE-295 (also missing timeout)")],
        "tests/test_fetch.py",
        *_test("fetch", "fetch"),
        focus="security",
    )
)
add(
    Case(
        "syn-020",
        "yaml.load without SafeLoader",
        "app/settings.py",
        "import yaml\n\n\ndef parse(text):\n    return yaml.safe_load(text)\n",
        "import yaml\n\n\ndef parse(text):\n    return yaml.load(text)\n",
        [("yaml.load(text)", "security", "CWE-502")],
        "tests/test_settings.py",
        *_test("settings", "parse"),
        focus="security",
    )
)
add(
    Case(
        "syn-021",
        "Predictable password-reset token",
        "app/reset.py",
        "import secrets\n\n\ndef reset_token():\n    return secrets.token_urlsafe(32)\n",
        "import random\n\n\ndef reset_token():\n    token = random.randint(100000, 999999)\n    return str(token)\n",
        [("token = random.randint", "security", "CWE-330")],
        "tests/test_reset.py",
        *_test("reset", "reset_token"),
        focus="security",
    )
)
add(
    Case(
        "syn-022",
        "subprocess with shell=True and f-string",
        "app/convert.py",
        "import subprocess\n\n\ndef convert(src, dst):\n    subprocess.run(['ffmpeg', '-i', src, dst], check=True)\n",
        "import subprocess\n\n\ndef convert(src, dst):\n    subprocess.run(f'ffmpeg -i {src} {dst}', shell=True)\n",
        [("shell=True", "security", "CWE-78")],
        "tests/test_convert.py",
        *_test("convert", "convert"),
        focus="security",
    )
)

# ---------------------------------------------------------------------- performance
add(
    Case(
        "syn-023",
        "Quadratic string building",
        "app/render.py",
        "def render(rows):\n    return ''.join(f'{r}\\n' for r in rows)\n",
        "def render(rows):\n    out = ''\n    for r in rows:\n        out += f'{r}\\n'\n    return out\n",
        [("out += f'{r}", "performance", "O(n^2) copying")],
        "tests/test_render.py",
        *_test("render", "render"),
        focus="performance",
    )
)
add(
    Case(
        "syn-024",
        "List membership inside loop",
        "app/dedupe.py",
        "def dedupe(items):\n    return list(dict.fromkeys(items))\n",
        "def dedupe(items):\n    seen = []\n    out = []\n    for item in items:\n        if item not in seen:\n            seen.append(item)\n            out.append(item)\n    return out\n",
        [("if item not in seen:", "performance", "O(n^2) membership")],
        "tests/test_dedupe.py",
        *_test("dedupe", "dedupe"),
        focus="performance",
    )
)
add(
    Case(
        "syn-025",
        "N+1 query in loop",
        "app/report.py",
        "def report(db, ids):\n    rows = db.execute('SELECT * FROM t WHERE id = ANY(%s)', (ids,)).fetchall()\n    return rows\n",
        "def report(db, ids):\n    rows = []\n    for i in ids:\n        rows.append(db.execute('SELECT * FROM t WHERE id = %s', (i,)).fetchone())\n    return rows\n",
        [("rows.append(db.execute(", "performance", "one query per id")],
        "tests/test_report.py",
        *_test("report", "report"),
        focus="performance",
    )
)
add(
    Case(
        "syn-026",
        "Regex compiled inside loop",
        "app/match.py",
        "import re\n\nPATTERN = re.compile(r'\\d+')\n\n\ndef count(lines):\n    return sum(1 for line in lines if PATTERN.search(line))\n",
        "import re\n\n\ndef count(lines):\n    n = 0\n    for line in lines:\n        pattern = re.compile(r'\\d+')\n        if pattern.search(line):\n            n += 1\n    return n\n",
        [("pattern = re.compile", "performance", "recompiled per iteration")],
        "tests/test_match.py",
        *_test("match", "count"),
        focus="performance",
    )
)
add(
    Case(
        "syn-027",
        "Blocking sleep in coroutine",
        "app/poller.py",
        "import asyncio\n\n\nasync def poll(check):\n    while not check():\n        await asyncio.sleep(1)\n",
        "import time\n\n\nasync def poll(check):\n    while not check():\n        time.sleep(1)\n",
        [("time.sleep(1)", "performance", "blocks the event loop")],
        "tests/test_poller.py",
        *_test("poller", "poll"),
        focus="performance",
    )
)

# ---------------------------------------------------------------------------- style
add(
    Case(
        "syn-028",
        "None compared with ==",
        "app/nullable.py",
        "def default(value, fallback):\n    return fallback if value is None else value\n",
        "def default(value, fallback):\n    if value == None:\n        return fallback\n    return value\n",
        [("if value == None:", "style", "E711")],
        "tests/test_nullable.py",
        *_test("nullable", "default"),
        focus="style",
    )
)
add(
    Case(
        "syn-029",
        "type() equality and print debugging",
        "app/coerce.py",
        "def coerce(value):\n    if isinstance(value, str):\n        return value.strip()\n    return value\n",
        "def coerce(value):\n    if type(value) == str:\n        print('coercing', value)\n        return value.strip()\n    return value\n",
        [
            ("if type(value) == str:", "style", "E721"),
            ("print('coercing'", "style", "print in prod"),
        ],
        "tests/test_coerce.py",
        *_test("coerce", "coerce"),
        focus="style",
    )
)
add(
    Case(
        "syn-030",
        "Builtin shadowed and TODO left behind",
        "app/ids.py",
        "def next_id(current):\n    return current + 1\n",
        "def next_id(current):\n    id = current + 1\n    # TODO handle overflow\n    return id\n",
        [
            ("    id = current + 1", "style", "shadows builtin"),
            ("# TODO handle overflow", "docs", "TODO marker"),
        ],
        "tests/test_ids.py",
        *_test("ids", "next_id"),
        focus="style",
    )
)

# ------------------------------------------------------------------------- test gap
add(
    Case(
        "syn-031",
        "New public function without tests",
        "app/pricing.py",
        "def net(price, tax):\n    return price * (1 + tax)\n",
        "def net(price, tax):\n    return price * (1 + tax)\n\n\ndef gross_margin(price, cost):\n    return (price - cost) / price\n",
        [("def gross_margin(price, cost):", "test_gap", "no test added")],
        focus="test_gap",
    )
)
add(
    Case(
        "syn-032",
        "Modified branch not covered by test changes",
        "app/shipping.py",
        "def cost(weight):\n    if weight > 10:\n        return 20\n    return 5\n",
        "def cost(weight):\n    if weight > 10:\n        return 20\n    if weight > 5:\n        return 10\n    return 5\n",
        [("if weight > 5:", "test_gap", "new branch untested")],
        "tests/test_other.py",
        "def test_other():\n    assert True\n",
        "def test_other():\n    assert True\n\n\ndef test_more():\n    assert 1\n",
        focus="test_gap",
    )
)
add(
    Case(
        "syn-033",
        "New method on service class without tests",
        "app/service.py",
        "class Service:\n    def ping(self):\n        return 'pong'\n",
        "class Service:\n    def ping(self):\n        return 'pong'\n\n    def restart(self):\n        self.stopped = True\n        return self.ping()\n",
        [("def restart(self):", "test_gap", "no test")],
        focus="test_gap",
    )
)

# ------------------------------------------------------------------------ clean PRs
add(
    Case(
        "syn-034",
        "Clean refactor: comprehension and docstring",
        "app/clean1.py",
        "def squares(xs):\n    out = []\n    for x in xs:\n        out.append(x * x)\n    return out\n",
        'def squares(xs):\n    """Return the squares of ``xs``."""\n    return [x * x for x in xs]\n',
        [],
        "tests/test_clean1.py",
        *_test("clean1", "squares", "assert squares([2]) == [4]"),
        focus="clean",
    )
)
add(
    Case(
        "syn-035",
        "Clean: explicit exception and logging",
        "app/clean2.py",
        "import logging\n\nlog = logging.getLogger(__name__)\n\n\ndef parse_port(text):\n    return int(text)\n",
        "import logging\n\nlog = logging.getLogger(__name__)\n\n\ndef parse_port(text):\n    try:\n        port = int(text)\n    except ValueError as exc:\n        log.warning('invalid port %r: %s', text, exc)\n        raise\n    if not 0 < port < 65536:\n        raise ValueError(f'port out of range: {port}')\n    return port\n",
        [],
        "tests/test_clean2.py",
        *_test("clean2", "parse_port", "assert parse_port('80') == 80"),
        focus="clean",
    )
)
add(
    Case(
        "syn-036",
        "Clean: dataclass and type hints",
        "app/clean3.py",
        "class Point:\n    def __init__(self, x, y):\n        self.x = x\n        self.y = y\n",
        "from dataclasses import dataclass\n\n\n@dataclass(frozen=True)\nclass Point:\n    x: float\n    y: float\n\n    def norm(self) -> float:\n        return (self.x**2 + self.y**2) ** 0.5\n",
        [],
        "tests/test_clean3.py",
        "from app.clean3 import Point\n\n\ndef test_point():\n    assert Point(0, 0)\n",
        "from app.clean3 import Point\n\n\ndef test_point():\n    assert Point(0, 0)\n\n\ndef test_norm():\n    assert Point(3, 4).norm() == 5\n",
        focus="clean",
    )
)
add(
    Case(
        "syn-037",
        "Clean: safe subprocess and timeout",
        "app/clean4.py",
        "import subprocess\n\n\ndef version():\n    return subprocess.run(['git', '--version'], capture_output=True, text=True).stdout\n",
        "import subprocess\n\n\ndef version() -> str:\n    result = subprocess.run(\n        ['git', '--version'], capture_output=True, text=True, timeout=5, check=True\n    )\n    return result.stdout.strip()\n",
        [],
        "tests/test_clean4.py",
        *_test("clean4", "version"),
        focus="clean",
    )
)
add(
    Case(
        "syn-038",
        "Clean: set-based membership and early return",
        "app/clean5.py",
        "def any_blocked(users, blocked):\n    for u in users:\n        if u in blocked:\n            return True\n    return False\n",
        "def any_blocked(users, blocked):\n    blocked_set = set(blocked)\n    return any(u in blocked_set for u in users)\n",
        [],
        "tests/test_clean5.py",
        *_test("clean5", "any_blocked", "assert any_blocked(['a'], ['a'])"),
        focus="clean",
    )
)


def main() -> int:
    """Write the cases file and print a short summary."""
    records = [c.build() for c in CASES]
    ids = [r["id"] for r in records]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate case ids")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_gt = sum(len(r["ground_truth"]) for r in records)  # type: ignore[arg-type]
    n_clean = sum(1 for r in records if r["clean"])
    sys.stdout.write(
        f"wrote {len(records)} cases ({n_clean} clean) with {n_gt} ground-truth items to {OUT}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
