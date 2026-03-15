"""Validation: nf-core lint + nf-test subprocess wrappers with failure classification.

Design note: code-to-module calls nf-core/tools as a subprocess
(via ``nf-core modules lint --json``) rather than importing its internal Python
API.  The --json output IS a deliberate, stable interface; internal Python classes
are refactored frequently without versioned guarantees.  Subprocess isolation also
means a nf-core/tools crash or import error cannot affect code-to-module.
"""

from __future__ import annotations

import json
import re
import subprocess
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from code_to_module.standards.loader import Standards

_MIN_NF_CORE_VERSION = (2, 14)

# ── Data models ────────────────────────────────────────────────────────────────


class FixClass(str, Enum):
    CLASS_A = "CLASS_A"   # rule-based fix available
    CLASS_B = "CLASS_B"   # LLM-assisted fix available
    CLASS_C = "CLASS_C"   # human only


class LintFailure(BaseModel):
    code: str
    message: str
    fix_class: FixClass
    fix_hint: str = ""


class NfTestFailure(BaseModel):
    test_name: str
    error_type: Literal[
        "file_not_found", "snapshot_mismatch", "process_failed", "unknown"
    ] = "unknown"
    error: str
    fix_class: FixClass
    fix_hint: str = ""
    is_missing_dependency: bool = False


class TestReport(BaseModel):
    module_path: str = ""
    lint_passed: bool = True
    nftest_passed: bool = True
    lint_failures: list[LintFailure] = []
    nftest_failures: list[NfTestFailure] = []
    class_a_count: int = 0
    class_b_count: int = 0
    class_c_count: int = 0
    lint_raw: str = ""
    nftest_raw: str = ""


# ── Version / availability helpers ────────────────────────────────────────────


def _parse_version(version_str: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", version_str)
    return tuple(int(p) for p in parts[:3]) if parts else (0,)


def check_nf_core_available() -> tuple[bool, str]:
    """Return (available, message). message is empty when available."""
    try:
        result = subprocess.run(
            ["nf-core", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        version_str = (result.stdout + result.stderr).strip()
        version_tuple = _parse_version(version_str)
        if version_tuple < _MIN_NF_CORE_VERSION:
            min_str = ".".join(str(v) for v in _MIN_NF_CORE_VERSION)
            found_str = ".".join(str(v) for v in version_tuple)
            return (
                False,
                f"nf-core {found_str} is too old — requires >={min_str}. "
                f"Upgrade: pip install 'nf-core>={min_str}'",
            )
        return True, ""
    except FileNotFoundError:
        return False, "nf-core tools not found. Install: pip install nf-core>=2.14"
    except subprocess.TimeoutExpired:
        return False, "nf-core --version timed out."


# ── Lint failure classification ────────────────────────────────────────────────

_LINT_CLASS_A: dict[str, str] = {
    "MODULE_MISSING_VERSIONS_TOPIC": (
        "Add topic: 'versions' to the versions emit channel."
    ),
    "MODULE_MISSING_EXT_ARGS": (
        "Add: def args = task.ext.args ?: '' to the script: block."
    ),
    "MODULE_CONTAINER_URL_FORMAT": (
        "Use quay.io/biocontainers/ (Docker) and "
        "https://depot.galaxyproject.org/singularity/ (Singularity) prefixes."
    ),
    "MODULE_META_YML_MISSING_FIELD": (
        "Add the missing required field to meta.yml."
    ),
    "MODULE_EMIT_NAME_MISMATCH": (
        "Ensure the emit: name in main.nf matches the channel name in meta.yml."
    ),
    "MODULE_CONDA_CHANNEL_ORDER": (
        "Reorder conda channels: conda-forge, bioconda, defaults."
    ),
}

_LINT_CLASS_B: dict[str, str] = {
    "MODULE_INCORRECT_OUTPUT_PATTERN": (
        "Review the glob pattern for this output channel — it may not match "
        "the actual tool output filenames."
    ),
    "MODULE_LABEL_INAPPROPRIATE": (
        "Consider whether the process label correctly reflects the compute "
        "resources this tool needs."
    ),
    "MODULE_DESCRIPTION_MISSING": (
        "Add a clear description to this channel in meta.yml."
    ),
}

_LINT_CLASS_C: dict[str, str] = {
    "MODULE_TEMPLATE_OUTDATED": (
        "Run: nf-core modules patch <module_name> to update to the latest template."
    ),
    "MODULE_TEST_DATA_MISSING": (
        "Add suitable test data to nf-core/test-datasets and reference it in "
        "tests/main.nf.test."
    ),
}


def _classify_lint(code: str, message: str) -> LintFailure:
    """Classify a single nf-core lint failure code into Class A / B / C."""
    if code in _LINT_CLASS_A:
        return LintFailure(
            code=code,
            message=message,
            fix_class=FixClass.CLASS_A,
            fix_hint=_LINT_CLASS_A[code],
        )
    if code in _LINT_CLASS_B:
        return LintFailure(
            code=code,
            message=message,
            fix_class=FixClass.CLASS_B,
            fix_hint=_LINT_CLASS_B[code],
        )
    if code in _LINT_CLASS_C:
        return LintFailure(
            code=code,
            message=message,
            fix_class=FixClass.CLASS_C,
            fix_hint=_LINT_CLASS_C[code],
        )
    # Unknown code — CLASS_C with a note
    return LintFailure(
        code=code,
        message=message,
        fix_class=FixClass.CLASS_C,
        fix_hint=f"Unrecognised lint code '{code}' — check the nf-core lint docs.",
    )


# ── nf-core lint runner ────────────────────────────────────────────────────────


def _parse_lint_json(raw_json: str) -> list[LintFailure]:
    """Parse nf-core modules lint --json output into LintFailure objects."""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return []

    failures: list[LintFailure] = []
    # nf-core lint JSON shape: {"lint_results": {"failed": [{"check_name": ..., "message": ...}]}}
    # Also handle flat list format
    failed_entries: list[dict[str, Any]] = []

    if isinstance(data, dict):
        results = data.get("lint_results", data)
        if isinstance(results, dict):
            failed_entries = results.get("failed", [])
        elif isinstance(results, list):
            failed_entries = results
    elif isinstance(data, list):
        failed_entries = data

    for entry in failed_entries:
        code = entry.get("check_name") or entry.get("code") or "UNKNOWN"
        message = entry.get("message") or entry.get("msg") or str(entry)
        failures.append(_classify_lint(str(code), str(message)))

    return failures


def _parse_lint_text(text: str) -> list[LintFailure]:
    """Fallback: parse human-readable nf-core lint output for ERROR lines."""
    failures: list[LintFailure] = []
    # Lines like: "  ✗  MODULE_MISSING_VERSIONS_TOPIC - ..."
    # or:         "[ERROR] MODULE_MISSING_VERSIONS_TOPIC: ..."
    pattern = re.compile(
        r"(?:✗|ERROR|FAILED)\s*[:\-]?\s*([A-Z_]+)\s*[-:]?\s*(.*)", re.IGNORECASE
    )
    for line in text.splitlines():
        m = pattern.search(line)
        if m:
            code = m.group(1).strip()
            message = m.group(2).strip() or line.strip()
            failures.append(_classify_lint(code, message))
    return failures


def _run_nf_core_lint(module_path: Path) -> tuple[list[LintFailure], str]:
    """Run nf-core modules lint and return (failures, raw_output)."""
    available, msg = check_nf_core_available()
    if not available:
        return (
            [_classify_lint("DEPENDENCY_MISSING", msg)],
            msg,
        )

    try:
        result = subprocess.run(
            ["nf-core", "modules", "lint", "--json", str(module_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        raw = result.stdout + result.stderr

        # Try JSON first, fall back to text parsing
        failures = _parse_lint_json(result.stdout)
        if not failures and result.returncode != 0:
            failures = _parse_lint_text(raw)

        return failures, raw

    except FileNotFoundError:
        msg = "nf-core tools not found. Install: pip install nf-core>=2.14"
        return [_classify_lint("DEPENDENCY_MISSING", msg)], msg
    except subprocess.TimeoutExpired:
        msg = "nf-core modules lint timed out."
        return [_classify_lint("TIMEOUT", msg)], msg


# ── nf-test failure classification ────────────────────────────────────────────


def _error_type(
    message: str,
) -> Literal["file_not_found", "snapshot_mismatch", "process_failed", "unknown"]:
    lower = message.lower()
    if "no such file" in lower or "filenotfound" in lower:
        return "file_not_found"
    if "snapshot" in lower or "mismatch" in lower:
        return "snapshot_mismatch"
    if "process failed" in lower or "process error" in lower:
        return "process_failed"
    return "unknown"


def _classify_nftest(
    test_name: str,
    message: str,
    module_path: Path,
) -> NfTestFailure:
    """Classify a single nf-test failure."""
    etype = _error_type(message)

    # CLASS_A: snapshot mismatch and snapshot file exists
    if etype == "snapshot_mismatch":
        snap_dir = module_path / "tests"
        snap_exists = any(snap_dir.glob("*.snap")) if snap_dir.exists() else False
        if snap_exists:
            return NfTestFailure(
                test_name=test_name,
                error_type=etype,
                error=message,
                fix_class=FixClass.CLASS_A,
                fix_hint=(
                    "Snapshot output has changed but is deterministic. "
                    "Run: nf-test test --update-snapshot to refresh."
                ),
            )

    # CLASS_B: file_not_found with glob-like path
    if etype == "file_not_found":
        # If the path in the error looks like a glob pattern
        glob_pattern = re.search(r"[\*\?\[\{]", message)
        # Check whether this is an input test data file (test data missing = CLASS_C)
        is_test_input = bool(re.search(r"test[_-]data|testdata|nf-core/test", message, re.I))
        if glob_pattern and not is_test_input:
            return NfTestFailure(
                test_name=test_name,
                error_type=etype,
                error=message,
                fix_class=FixClass.CLASS_B,
                fix_hint=(
                    "The output filename glob pattern probably does not match "
                    "the actual tool output. Review the pattern in main.nf."
                ),
            )
        if is_test_input:
            return NfTestFailure(
                test_name=test_name,
                error_type=etype,
                error=message,
                fix_class=FixClass.CLASS_C,
                fix_hint=(
                    "Test input data is missing. Add suitable files to "
                    "nf-core/test-datasets and update tests/main.nf.test."
                ),
            )

    # CLASS_B: process failed with "command not found"
    if etype == "process_failed" and "command not found" in message.lower():
        return NfTestFailure(
            test_name=test_name,
            error_type=etype,
            error=message,
            fix_class=FixClass.CLASS_B,
            fix_hint=(
                "The container is missing a required tool. "
                "Check the container definition or use a different image."
            ),
        )

    # CLASS_C: process failed with tool-specific error (not command-not-found)
    if etype == "process_failed":
        return NfTestFailure(
            test_name=test_name,
            error_type=etype,
            error=message,
            fix_class=FixClass.CLASS_C,
            fix_hint=(
                "A biological or logic error occurred in the module. "
                "Review the error output and check the tool invocation in main.nf."
            ),
        )

    # CLASS_C: unknown
    return NfTestFailure(
        test_name=test_name,
        error_type=etype,
        error=message,
        fix_class=FixClass.CLASS_C,
        fix_hint="Cannot classify automatically — review the full test output.",
    )


def _parse_nftest_output(text: str, module_path: Path) -> list[NfTestFailure]:
    """Parse nf-test --verbose output into NfTestFailure objects."""
    failures: list[NfTestFailure] = []
    lines = text.splitlines()

    # Identify failure blocks: lines with FAILED / ✘ followed by test name
    i = 0
    while i < len(lines):
        line = lines[i]
        # Match "FAILED  TestName" or "✘ TestName" or "  ✘  TestName"
        m = re.search(r"(?:FAILED|✘)\s+(.+)", line)
        if m:
            test_name = m.group(1).strip()
            # Collect subsequent lines as the error context (up to next failure or blank section)
            error_lines: list[str] = []
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                # Stop at another failure marker or section separator
                if re.search(r"(?:FAILED|✘|PASSED|✔|SUCCESS)\s+", next_line):
                    break
                if next_line.strip().startswith("---") and len(next_line.strip()) > 6:
                    break
                error_lines.append(next_line)
                j += 1
            error_msg = "\n".join(error_lines).strip() or line
            failures.append(_classify_nftest(test_name, error_msg, module_path))
            i = j
        else:
            i += 1

    return failures


def _run_nf_test(module_path: Path) -> tuple[list[NfTestFailure], str]:
    """Run nf-test and return (failures, raw_output)."""
    test_file = module_path / "tests" / "main.nf.test"
    if not test_file.exists():
        msg = f"nf-test file not found: {test_file}"
        return (
            [
                NfTestFailure(
                    test_name="setup",
                    error_type="file_not_found",
                    error=msg,
                    fix_class=FixClass.CLASS_C,
                    fix_hint="Create tests/main.nf.test for this module.",
                )
            ],
            msg,
        )

    try:
        result = subprocess.run(
            ["nf-test", "test", str(test_file), "--verbose"],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=module_path,
        )
        raw = result.stdout + result.stderr
        if result.returncode == 0:
            return [], raw
        failures = _parse_nftest_output(raw, module_path)
        if not failures:
            # returncode != 0 but no failures parsed — add a generic one
            failures = [
                NfTestFailure(
                    test_name="nf-test",
                    error_type="unknown",
                    error=raw[:2000],
                    fix_class=FixClass.CLASS_C,
                    fix_hint="Review the full nf-test output for details.",
                )
            ]
        return failures, raw

    except FileNotFoundError:
        msg = (
            "nf-test not found. Install: "
            "curl -fsSL https://get.nf-test.com | bash && mv nf-test /usr/local/bin/"
        )
        return (
            [
                NfTestFailure(
                    test_name="dependency_check",
                    error_type="unknown",
                    error=msg,
                    fix_class=FixClass.CLASS_C,
                    fix_hint=msg,
                    is_missing_dependency=True,
                )
            ],
            msg,
        )
    except subprocess.TimeoutExpired:
        msg = "nf-test timed out."
        return (
            [
                NfTestFailure(
                    test_name="nf-test",
                    error_type="unknown",
                    error=msg,
                    fix_class=FixClass.CLASS_C,
                    fix_hint="Increase timeout or check for hanging processes.",
                )
            ],
            msg,
        )


# ── Rich report ────────────────────────────────────────────────────────────────


def _print_report(report: TestReport, console: Console) -> None:
    module_name = Path(report.module_path).name if report.module_path else "module"

    lint_status = "✓  passed" if report.lint_passed else f"✗  {len(report.lint_failures)} failure{'s' if len(report.lint_failures) != 1 else ''}"
    nftest_status = "✓  passed" if report.nftest_passed else f"✗  {len(report.nftest_failures)} failure{'s' if len(report.nftest_failures) != 1 else ''}"

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("item", style="bold")
    table.add_column("status")
    table.add_row("nf-core lint", f"[{'green' if report.lint_passed else 'red'}]{lint_status}[/]")
    table.add_row("nf-test     ", f"[{'green' if report.nftest_passed else 'red'}]{nftest_status}[/]")
    table.add_row("", "")
    table.add_row(
        "Class A (auto-fixable)",
        f"[cyan]{report.class_a_count}[/]{'   run: code-to-module fix' if report.class_a_count else ''}",
    )
    table.add_row(
        "Class B (LLM-assisted)",
        f"[yellow]{report.class_b_count}[/]{'   run: code-to-module fix' if report.class_b_count else ''}",
    )
    table.add_row(
        "Class C (human only)  ",
        f"[red]{report.class_c_count}[/]{'   see details below' if report.class_c_count else ''}",
    )

    border = "green" if (report.lint_passed and report.nftest_passed) else "red"
    console.print(Panel(table, title=f"[bold]Validation: {module_name}[/bold]", border_style=border))

    # Print Class C details
    class_c: list[LintFailure | NfTestFailure] = [
        f for f in report.lint_failures if f.fix_class == FixClass.CLASS_C
    ] + [f for f in report.nftest_failures if f.fix_class == FixClass.CLASS_C]

    if class_c:
        console.print("[bold red]Class C failures require manual intervention:[/bold red]")
        for failure in class_c:
            if isinstance(failure, LintFailure):
                console.print(f"  [red]✗[/red] [lint] {failure.code}")
                console.print(f"    {failure.message}")
            else:
                console.print(f"  [red]✗[/red] [nf-test] {failure.test_name}")
                console.print(f"    {failure.error[:200]}")
            console.print(f"    [dim]Fix: {failure.fix_hint}[/dim]")


# ── Main entry point ───────────────────────────────────────────────────────────


def run_validation(
    module_path: Path,
    standards: Standards,
    lint_only: bool = False,
    nftest_only: bool = False,
    console: Console | None = None,
) -> TestReport:
    """Run nf-core lint and/or nf-test, classify all failures, print a report.

    Never raises. Returns a populated TestReport.
    """
    if console is None:
        console = Console()

    lint_failures: list[LintFailure] = []
    nftest_failures: list[NfTestFailure] = []
    lint_raw = ""
    nftest_raw = ""

    if not nftest_only:
        lint_failures, lint_raw = _run_nf_core_lint(module_path)

    if not lint_only:
        nftest_failures, nftest_raw = _run_nf_test(module_path)

    all_failures: list[LintFailure | NfTestFailure] = list(lint_failures) + list(nftest_failures)
    class_a = sum(1 for f in all_failures if f.fix_class == FixClass.CLASS_A)
    class_b = sum(1 for f in all_failures if f.fix_class == FixClass.CLASS_B)
    class_c = sum(1 for f in all_failures if f.fix_class == FixClass.CLASS_C)

    report = TestReport(
        module_path=str(module_path),
        lint_passed=len(lint_failures) == 0,
        nftest_passed=len(nftest_failures) == 0,
        lint_failures=lint_failures,
        nftest_failures=nftest_failures,
        class_a_count=class_a,
        class_b_count=class_b,
        class_c_count=class_c,
        lint_raw=lint_raw,
        nftest_raw=nftest_raw,
    )

    _print_report(report, console)
    return report
