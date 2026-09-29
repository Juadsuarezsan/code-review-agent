"""Security Scanner tests: pattern rules and the semgrep adapter (mocked subprocess)."""

import json
import subprocess

import pytest
from pytest_mock import MockerFixture

from src.analyzers.security_scanner import (
    PatternSecurityScanner,
    SecurityScanError,
    SemgrepScanner,
    get_security_scanner,
    parse_semgrep_json,
    scan_security,
)
from src.parser.diff_parser import FileDiff, parse_unified_diff


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ("    return eval(expr)", "code-injection-eval"),
        ("    os.system('ls ' + path)", "shell-injection-system"),
        ("    subprocess.run(cmd, shell=True)", "shell-injection-shell-true"),
        ("    data = pickle.loads(blob)", "unsafe-deserialization-pickle"),
        ("    cfg = yaml.load(fh)", "unsafe-yaml-load"),
        ('    cur.execute(f"SELECT * FROM t WHERE id={uid}")', "sql-injection-format"),
        ('    cur.execute("SELECT * FROM t WHERE id={}".format(uid))', "sql-injection-format-call"),
        ('    password = "hunter2secret"', "hardcoded-secret"),
        ("    r = requests.get(url, verify=False, timeout=3)", "tls-verify-disabled"),
        ("    h = hashlib.md5(pw.encode())", "weak-hash"),
        ("    token = random.randint(0, 999999)", "insecure-random"),
        ("    tmp = tempfile.mktemp()", "insecure-tempfile"),
        ("    app.run(debug=True)", "debug-enabled"),
        ("    env = Environment(autoescape=False)", "xss-autoescape-off"),
        ("    os.chmod(path, 0o777)", "world-writable-chmod"),
        ("    r = requests.get(url)", "http-no-timeout"),
        ('    subprocess.run(f"rm {name}")', "subprocess-fstring"),
    ],
)
def test_pattern_rules(line: str, rule: str) -> None:
    findings = PatternSecurityScanner().scan("x.py", line + "\n")
    assert rule in {f.rule_id for f in findings}
    assert all(f.cwe for f in findings)


@pytest.mark.parametrize(
    "line",
    [
        "    cfg = yaml.load(fh, Loader=yaml.SafeLoader)",
        '    api_key = "<your-api-key-placeholder>"',
        '    api_key = os.environ["API_KEY"]',
        "    r = requests.get(url, timeout=10)",
        "    h = hashlib.md5(data, usedforsecurity=False)",
        "    x = random.random()  # jitter, not a secret",
        "# os.system('commented out')",
    ],
)
def test_pattern_rule_exclusions(line: str) -> None:
    assert PatternSecurityScanner().scan("x.py", line + "\n") == []


def test_scan_security_anchors_only_added_lines() -> None:
    diff = """--- a/m.py
+++ b/m.py
@@ -1,2 +1,3 @@
 import os
 os.system("old")
+os.system(cmd)
"""
    comments = scan_security(parse_unified_diff(diff)[0], PatternSecurityScanner())
    assert [c.line for c in comments] == [3]
    assert comments[0].category == "security" and "CWE-78" in comments[0].body
    assert comments[0].rule_id == "pattern:shell-injection-system"


def test_deleted_file_is_skipped() -> None:
    assert scan_security(FileDiff(path="x.py", added=[(1, "eval(x)")], is_deleted=True)) == []


SEMGREP_JSON = {
    "results": [
        {
            "check_id": "python.lang.security.audit.eval-detected",
            "start": {"line": 2},
            "extra": {
                "message": "Detected eval",
                "severity": "ERROR",
                "metadata": {"cwe": ["CWE-95: Eval Injection"]},
                "fix": None,
            },
        }
    ],
    "errors": [],
}


def test_parse_semgrep_json() -> None:
    findings = parse_semgrep_json(json.dumps(SEMGREP_JSON))
    assert findings[0].line == 2 and findings[0].rule_id == "eval-detected"
    assert findings[0].cwe == "CWE-95" and findings[0].severity == "error"
    assert parse_semgrep_json("") == []
    with pytest.raises(SecurityScanError):
        parse_semgrep_json("{oops")


def test_semgrep_scanner_runs_subprocess(mocker: MockerFixture) -> None:
    run = mocker.patch(
        "src.analyzers.security_scanner.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout=json.dumps(SEMGREP_JSON), stderr=""
        ),
    )
    scanner = SemgrepScanner("/usr/bin/semgrep")
    findings = scanner.scan("x.py", "import os\neval(x)\n")
    assert findings[0].rule_id == "eval-detected"
    argv = run.call_args.args[0]
    assert argv[0] == "/usr/bin/semgrep" and "--json" in argv


def test_semgrep_failure_modes(mocker: MockerFixture) -> None:
    mocker.patch(
        "src.analyzers.security_scanner.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=2, stdout="", stderr="bad"),
    )
    with pytest.raises(SecurityScanError):
        SemgrepScanner("/usr/bin/semgrep").scan("x.py", "x = 1\n")
    mocker.patch(
        "src.analyzers.security_scanner.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="semgrep", timeout=1),
    )
    with pytest.raises(SecurityScanError):
        SemgrepScanner("/usr/bin/semgrep", timeout_seconds=1).scan("x.py", "x = 1\n")
    # scan_security swallows scanner errors after logging them
    assert (
        scan_security(
            FileDiff(path="x.py", added=[(1, "eval(x)")]), SemgrepScanner("/usr/bin/semgrep")
        )
        == []
    )


def test_get_security_scanner_fallback(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert get_security_scanner(None).name == "pattern"
    assert get_security_scanner(str(tmp_path / "missing")).name == "pattern"
    fake = tmp_path / "semgrep"
    fake.write_text("#!/bin/sh\n")
    assert get_security_scanner(str(fake)).name == "semgrep"
