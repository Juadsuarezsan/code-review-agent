"""Security Scanner: semgrep when available, a CWE-annotated pattern scanner otherwise.

Both implementations satisfy :class:`SecurityScanner`. :func:`get_security_scanner`
picks ``semgrep`` only when an executable path is configured and exists, because the
``semgrep`` wheel pulls a large dependency tree that is not installed by default
(``pip install -e ".[security]"``).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

from src.api.schemas import ReviewComment, Severity
from src.parser.diff_parser import FileDiff


class SecurityScanError(RuntimeError):
    """Raised when the scanner process fails."""


@dataclass(frozen=True)
class SecurityFinding:
    """One security diagnostic."""

    line: int
    rule_id: str
    message: str
    severity: Severity
    cwe: str | None = None
    suggestion: str | None = None
    confidence: float = 0.8


class SecurityScanner(Protocol):
    """Interface for security scanners."""

    name: str

    def scan(self, path: str, source: str) -> list[SecurityFinding]:
        """Scan ``source`` (new-side snippet) and return findings with real line numbers."""
        ...


@dataclass(frozen=True)
class _PatternRule:
    rule_id: str
    pattern: re.Pattern[str]
    severity: Severity
    cwe: str
    message: str
    suggestion: str | None = None
    confidence: float = 0.8
    requires: re.Pattern[str] | None = None
    excludes: re.Pattern[str] | None = None


def _p(
    rule_id: str,
    pattern: str,
    severity: Severity,
    cwe: str,
    message: str,
    *,
    suggestion: str | None = None,
    confidence: float = 0.8,
    requires: str | None = None,
    excludes: str | None = None,
) -> _PatternRule:
    return _PatternRule(
        rule_id,
        re.compile(pattern),
        severity,
        cwe,
        message,
        suggestion,
        confidence,
        re.compile(requires, re.IGNORECASE) if requires else None,
        re.compile(excludes) if excludes else None,
    )


PATTERN_RULES: list[_PatternRule] = [
    _p(
        "code-injection-eval",
        r"\beval\s*\(",
        "error",
        "CWE-95",
        "`eval()` executes arbitrary code from its argument.",
        suggestion="ast.literal_eval(value)  # for literals",
        confidence=0.9,
    ),
    _p(
        "code-injection-exec",
        r"\bexec\s*\(",
        "error",
        "CWE-95",
        "`exec()` executes arbitrary code from its argument.",
        confidence=0.9,
    ),
    _p(
        "shell-injection-system",
        r"\bos\.(system|popen)\s*\(",
        "error",
        "CWE-78",
        "`os.system`/`os.popen` run through a shell; user input becomes a command.",
        suggestion='subprocess.run(["cmd", arg], check=True)',
        confidence=0.9,
    ),
    _p(
        "shell-injection-shell-true",
        r"shell\s*=\s*True",
        "error",
        "CWE-78",
        "`shell=True` enables command injection when any argument is user controlled.",
        suggestion='subprocess.run(["cmd", arg], shell=False)',
        confidence=0.85,
    ),
    _p(
        "unsafe-deserialization-pickle",
        r"\b(pickle|cPickle|marshal|shelve)\.loads?\s*\(",
        "error",
        "CWE-502",
        "Deserializing untrusted data with pickle/marshal is remote code execution.",
        suggestion="json.loads(payload)",
        confidence=0.9,
    ),
    _p(
        "unsafe-yaml-load",
        r"\byaml\.load\s*\(",
        "error",
        "CWE-502",
        "`yaml.load` without `SafeLoader` instantiates arbitrary Python objects.",
        suggestion="yaml.safe_load(stream)",
        confidence=0.9,
        excludes=r"SafeLoader|safe_load|Loader\s*=\s*yaml\.CSafeLoader",
    ),
    _p(
        "sql-injection-format",
        r"\.(execute|executemany|raw)\s*\(\s*(f[\"']|[\"'].*%\s*\(|.*\+\s*\w)",
        "error",
        "CWE-89",
        "SQL statement built with string formatting; use parameter binding.",
        suggestion='cursor.execute("SELECT ... WHERE id = %s", (user_id,))',
        confidence=0.85,
    ),
    _p(
        "sql-injection-format-call",
        r"\.(execute|executemany|raw)\s*\(.*\.format\(",
        "error",
        "CWE-89",
        "SQL statement built with `.format()`; use parameter binding.",
        confidence=0.85,
    ),
    _p(
        "hardcoded-secret",
        r"(?i)\b\w*(password|passwd|pwd|secret|api_?key|token|private_key)\w*\s*[:=]\s*"
        r"[\"'][^\"']{6,}[\"']",
        "error",
        "CWE-798",
        "Hard-coded credential in source code.",
        suggestion='os.environ["API_KEY"]  # load from the environment or a secret manager',
        confidence=0.75,
        excludes=r"(?i)example|placeholder|dummy|changeme|<|\{\{|\$\{|os\.environ|getenv",
    ),
    _p(
        "tls-verify-disabled",
        r"verify\s*=\s*False",
        "warning",
        "CWE-295",
        "TLS certificate verification disabled; enables man-in-the-middle attacks.",
        confidence=0.85,
    ),
    _p(
        "weak-hash",
        r"\bhashlib\.(md5|sha1)\s*\(",
        "warning",
        "CWE-328",
        "MD5/SHA-1 are broken for security purposes.",
        suggestion="hashlib.sha256(data).hexdigest()",
        confidence=0.7,
        excludes=r"usedforsecurity\s*=\s*False",
    ),
    _p(
        "insecure-random",
        r"\brandom\.(random|randint|choice|choices|randrange|getrandbits)\s*\(",
        "warning",
        "CWE-330",
        "`random` is not cryptographically secure for tokens or secrets.",
        suggestion="secrets.token_urlsafe(32)",
        confidence=0.8,
        requires=r"token|secret|password|salt|nonce|otp|session|key",
    ),
    _p(
        "insecure-tempfile",
        r"\btempfile\.mktemp\s*\(",
        "warning",
        "CWE-377",
        "`tempfile.mktemp` is racy; the file can be hijacked between creation and use.",
        suggestion="tempfile.NamedTemporaryFile(delete=False)",
        confidence=0.9,
    ),
    _p(
        "debug-enabled",
        r"\bdebug\s*=\s*True",
        "warning",
        "CWE-489",
        "Debug mode enabled; exposes the interactive debugger and stack traces.",
        confidence=0.6,
        requires=r"app\.run|run\(|DEBUG|Flask|debug\s*=\s*True\s*\)",
    ),
    _p(
        "xss-autoescape-off",
        r"autoescape\s*=\s*False",
        "warning",
        "CWE-79",
        "Template autoescaping disabled; user data is rendered as raw HTML.",
        confidence=0.8,
    ),
    _p(
        "xxe-xml-parse",
        r"\b(xml\.etree\.ElementTree|ElementTree|etree|minidom)\.(parse|fromstring|parseString)\s*\(",
        "info",
        "CWE-611",
        "Standard XML parsers are vulnerable to entity expansion attacks on untrusted input.",
        suggestion="defusedxml.ElementTree.fromstring(data)",
        confidence=0.5,
    ),
    _p(
        "world-writable-chmod",
        r"\bos\.chmod\s*\([^)]*0o?7[67]7",
        "warning",
        "CWE-732",
        "World-writable permissions granted.",
        confidence=0.85,
    ),
    _p(
        "bind-all-interfaces",
        r"[\"']0\.0\.0\.0[\"']",
        "info",
        "CWE-605",
        "Binding to 0.0.0.0 exposes the service on every network interface.",
        confidence=0.5,
    ),
    _p(
        "http-no-timeout",
        r"\b(requests|httpx)\.(get|post|put|delete|patch|head|request)\s*\(",
        "warning",
        "CWE-400",
        "HTTP call without `timeout=`; a stalled server hangs the process.",
        suggestion="requests.get(url, timeout=10)",
        confidence=0.8,
        excludes=r"timeout\s*=",
    ),
    _p(
        "path-traversal-join",
        r"os\.path\.join\([^)]*(request|params|args|form|user|filename)",
        "warning",
        "CWE-22",
        "Path built from user input without normalisation; enables `../` traversal.",
        suggestion="Path(base).joinpath(name).resolve().relative_to(base)",
        confidence=0.6,
    ),
    _p(
        "ssrf-user-url",
        r"\b(requests|httpx|urllib\.request)\.(get|post|urlopen)\s*\(\s*(request\.|params|args|url_from|user_url)",
        "warning",
        "CWE-918",
        "Outbound request to a user-supplied URL (SSRF).",
        confidence=0.6,
    ),
    _p(
        "subprocess-fstring",
        r"subprocess\.(run|Popen|call|check_output)\s*\(\s*f[\"']",
        "error",
        "CWE-78",
        "Command assembled with an f-string; arguments are not escaped.",
        suggestion='subprocess.run(["cmd", arg])',
        confidence=0.85,
    ),
    _p(
        "dynamic-import",
        r"\b__import__\s*\(\s*[^\"']",
        "info",
        "CWE-94",
        "Dynamic import of a non-literal module name.",
        confidence=0.5,
    ),
]


def _strip_trailing_comment(line: str) -> str:
    """Drop a ``# comment`` suffix, ignoring ``#`` characters inside string literals."""
    quote: str | None = None
    for idx, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#":
            return line[:idx]
    return line


class PatternSecurityScanner:
    """Regex scanner annotated with CWE identifiers; always available."""

    name = "pattern"

    def scan(self, path: str, source: str) -> list[SecurityFinding]:
        """Scan every line of ``source`` against :data:`PATTERN_RULES`."""
        findings: list[SecurityFinding] = []
        for idx, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            code = _strip_trailing_comment(line)
            for rule in PATTERN_RULES:
                if not rule.pattern.search(code):
                    continue
                if rule.requires and not rule.requires.search(code):
                    continue
                if rule.excludes and rule.excludes.search(code):
                    continue
                findings.append(
                    SecurityFinding(
                        line=idx,
                        rule_id=rule.rule_id,
                        message=rule.message,
                        severity=rule.severity,
                        cwe=rule.cwe,
                        suggestion=rule.suggestion,
                        confidence=rule.confidence,
                    )
                )
        return findings


class SemgrepScanner:
    """Run the ``semgrep`` CLI with the community Python ruleset."""

    name = "semgrep"

    def __init__(
        self, binary: str, config: str = "p/python", timeout_seconds: float = 60.0
    ) -> None:
        self.binary = binary
        self.config = config
        self.timeout_seconds = timeout_seconds

    def scan(self, path: str, source: str) -> list[SecurityFinding]:
        """Write the snippet to a temp file, run semgrep and parse ``results``."""
        suffix = Path(path).suffix or ".py"
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as fh:
            fh.write(source)
            tmp_path = fh.name
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [self.binary, "--config", self.config, "--json", "--quiet", tmp_path],
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SecurityScanError(f"semgrep timed out after {self.timeout_seconds}s") from exc
        except OSError as exc:
            raise SecurityScanError(f"cannot start semgrep: {exc}") from exc
        finally:
            try:
                os.unlink(tmp_path)
            except OSError as exc:
                logger.debug("could not remove temp file {}: {}", tmp_path, exc)
        if proc.returncode not in (0, 1):
            raise SecurityScanError(
                f"semgrep exited {proc.returncode}: {proc.stderr.strip()[:300]}"
            )
        return parse_semgrep_json(proc.stdout)


def parse_semgrep_json(payload: str) -> list[SecurityFinding]:
    """Convert semgrep's JSON report into :class:`SecurityFinding` objects."""
    if not payload.strip():
        return []
    try:
        data: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SecurityScanError(f"semgrep produced invalid JSON: {exc}") from exc
    findings: list[SecurityFinding] = []
    for item in data.get("results", []) if isinstance(data, dict) else []:
        extra = item.get("extra", {})
        meta = extra.get("metadata", {}) or {}
        cwe_raw = meta.get("cwe")
        cwe = None
        if isinstance(cwe_raw, list) and cwe_raw:
            cwe = str(cwe_raw[0]).split(":")[0]
        elif isinstance(cwe_raw, str):
            cwe = cwe_raw.split(":")[0]
        sev_raw = str(extra.get("severity", "WARNING")).upper()
        severity: Severity = (
            "error" if sev_raw == "ERROR" else "warning" if sev_raw == "WARNING" else "info"
        )
        findings.append(
            SecurityFinding(
                line=int(item.get("start", {}).get("line", 0)),
                rule_id=str(item.get("check_id", "semgrep")).split(".")[-1],
                message=str(extra.get("message", "")).strip(),
                severity=severity,
                cwe=cwe,
                suggestion=extra.get("fix"),
                confidence=0.85,
            )
        )
    return findings


def get_security_scanner(semgrep_bin: str | None) -> SecurityScanner:
    """Return :class:`SemgrepScanner` when the binary exists, else the pattern scanner."""
    if semgrep_bin and Path(semgrep_bin).exists():
        return SemgrepScanner(semgrep_bin)
    if semgrep_bin:
        logger.warning("SEMGREP_BIN={} not found; using pattern scanner", semgrep_bin)
    return PatternSecurityScanner()


def scan_security(
    file_diff: FileDiff, scanner: SecurityScanner | None = None
) -> list[ReviewComment]:
    """Scan one file and return security comments anchored to added lines."""
    if not file_diff.added or file_diff.is_deleted:
        return []
    scanner = scanner or PatternSecurityScanner()
    try:
        findings = scanner.scan(file_diff.path, file_diff.new_snippet())
    except SecurityScanError as exc:
        logger.error(
            "security scan failed path={} scanner={} err={}", file_diff.path, scanner.name, exc
        )
        return []
    added = file_diff.added_line_numbers
    comments: list[ReviewComment] = []
    for f in findings:
        if f.line not in added:
            continue
        cwe = f" [{f.cwe}]" if f.cwe else ""
        comments.append(
            ReviewComment(
                file=file_diff.path,
                line=f.line,
                severity=f.severity,
                category="security",
                body=f"{f.message}{cwe}",
                suggestion=f.suggestion,
                justification=f"Matches {scanner.name} rule `{f.rule_id}`"
                + (f" ({f.cwe})." if f.cwe else "."),
                rule_id=f"{scanner.name}:{f.rule_id}",
                source=scanner.name,
                confidence=f.confidence,
            )
        )
    return comments
