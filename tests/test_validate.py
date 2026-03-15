"""Tests for validate.py — nf-core lint + nf-test classification.

All subprocess calls are mocked. Real nf-core lint / nf-test are never invoked
in unit tests — only in @pytest.mark.integration tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from code_to_module.validate import (
    FixClass,
    TestReport,
    _classify_lint,
    _classify_nftest,
    _parse_lint_json,
    run_validation,
)

# ── Fixture paths ──────────────────────────────────────────────────────────────

_FIXTURES = Path(__file__).parent / "fixtures" / "modules"
_PASSING = _FIXTURES / "passing"
_FAILING_LINT = _FIXTURES / "failing_lint"
_FAILING_NFTEST = _FIXTURES / "failing_nftest"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _lint_json(failures: list[dict]) -> str:
    """Build a minimal nf-core --json lint output string."""
    return json.dumps({"lint_results": {"failed": failures}})


def _nftest_output(test_name: str, error_msg: str) -> str:
    """Build minimal nf-test --verbose output with one failure."""
    return f"FAILED  {test_name}\n{error_msg}\nPASSED  some_other_test\n"


# ── Unit tests: _classify_lint ─────────────────────────────────────────────────


def test_classify_lint_class_a() -> None:
    """Known CLASS_A lint codes are classified correctly."""
    failure = _classify_lint("MODULE_MISSING_VERSIONS_TOPIC", "versions channel missing topic")
    assert failure.fix_class == FixClass.CLASS_A
    assert failure.code == "MODULE_MISSING_VERSIONS_TOPIC"
    assert failure.fix_hint != ""


def test_classify_lint_class_b() -> None:
    """Known CLASS_B lint codes are classified correctly."""
    failure = _classify_lint("MODULE_INCORRECT_OUTPUT_PATTERN", "glob pattern incorrect")
    assert failure.fix_class == FixClass.CLASS_B
    assert failure.fix_hint != ""


def test_classify_lint_class_c_known() -> None:
    """Known CLASS_C codes (e.g. MODULE_TEMPLATE_OUTDATED) are classified correctly."""
    failure = _classify_lint("MODULE_TEMPLATE_OUTDATED", "template is outdated")
    assert failure.fix_class == FixClass.CLASS_C
    assert failure.fix_hint != ""


def test_classify_lint_unknown_code_is_class_c() -> None:
    """Unknown lint codes fall back to CLASS_C with a note."""
    failure = _classify_lint("SOME_UNKNOWN_CODE_XYZ", "unrecognised failure")
    assert failure.fix_class == FixClass.CLASS_C
    assert "SOME_UNKNOWN_CODE_XYZ" in failure.fix_hint


# ── Unit tests: _parse_lint_json ──────────────────────────────────────────────


def test_parse_lint_json_class_a_and_b() -> None:
    """JSON with a CLASS_A and a CLASS_B failure are both parsed."""
    raw = _lint_json([
        {"check_name": "MODULE_MISSING_VERSIONS_TOPIC", "message": "missing topic channel"},
        {"check_name": "MODULE_INCORRECT_OUTPUT_PATTERN", "message": "wrong glob"},
    ])
    failures = _parse_lint_json(raw)
    assert len(failures) == 2
    classes = {f.fix_class for f in failures}
    assert FixClass.CLASS_A in classes
    assert FixClass.CLASS_B in classes


def test_parse_lint_json_empty_failures() -> None:
    """JSON with no failures returns empty list."""
    raw = _lint_json([])
    assert _parse_lint_json(raw) == []


def test_parse_lint_json_invalid_returns_empty() -> None:
    """Non-JSON text returns empty list (not an exception)."""
    assert _parse_lint_json("not valid json {{{") == []


# ── Unit tests: _classify_nftest ──────────────────────────────────────────────


def test_classify_nftest_snapshot_mismatch_class_a(tmp_path: Path) -> None:
    """Snapshot mismatch + .snap file exists → CLASS_A."""
    snap_dir = tmp_path / "tests"
    snap_dir.mkdir()
    (snap_dir / "main.nf.test.snap").write_text("{}")

    failure = _classify_nftest("test sort", "snapshot mismatch detected", tmp_path)
    assert failure.fix_class == FixClass.CLASS_A
    assert failure.error_type == "snapshot_mismatch"


def test_classify_nftest_file_not_found_glob_class_b(tmp_path: Path) -> None:
    """file_not_found with glob-like path → CLASS_B."""
    failure = _classify_nftest("test sort", "No such file: *.sorted.bam output", tmp_path)
    assert failure.fix_class == FixClass.CLASS_B
    assert failure.error_type == "file_not_found"


def test_classify_nftest_missing_test_data_class_c(tmp_path: Path) -> None:
    """file_not_found referencing test-datasets → CLASS_C."""
    failure = _classify_nftest(
        "test sort",
        "No such file: nf-core/test-datasets/genomics/sarscov2/test.bam",
        tmp_path,
    )
    assert failure.fix_class == FixClass.CLASS_C
    assert failure.error_type == "file_not_found"


def test_classify_nftest_command_not_found_class_b(tmp_path: Path) -> None:
    """process failed with 'command not found' → CLASS_B."""
    failure = _classify_nftest("test sort", "process failed: command not found: samtools", tmp_path)
    assert failure.fix_class == FixClass.CLASS_B
    assert failure.error_type == "process_failed"


# ── Integration-level tests: run_validation with mocked subprocesses ──────────


def test_passing_module(tmp_path: Path) -> None:
    """All lint passes + nf-test passes → report shows no failures."""
    test_file = tmp_path / "tests" / "main.nf.test"
    test_file.parent.mkdir()
    test_file.write_text("nextflow_process {}")

    with (
        patch("code_to_module.validate.check_nf_core_available", return_value=(True, "")),
        patch("code_to_module.validate.subprocess.run") as mock_run,
    ):
        lint_proc = MagicMock()
        lint_proc.returncode = 0
        lint_proc.stdout = _lint_json([])
        lint_proc.stderr = ""

        nftest_proc = MagicMock()
        nftest_proc.returncode = 0
        nftest_proc.stdout = "PASSED  test sort\n"
        nftest_proc.stderr = ""

        mock_run.side_effect = [lint_proc, nftest_proc]

        from code_to_module.standards.loader import Standards
        report = run_validation(tmp_path, Standards())

    assert report.lint_passed is True
    assert report.nftest_passed is True
    assert report.lint_failures == []
    assert report.nftest_failures == []
    assert report.class_a_count == 0
    assert report.class_b_count == 0
    assert report.class_c_count == 0


def test_lint_class_a_detection(tmp_path: Path) -> None:
    """Lint failure with CLASS_A code is captured and counted correctly."""
    test_file = tmp_path / "tests" / "main.nf.test"
    test_file.parent.mkdir()
    test_file.write_text("nextflow_process {}")

    lint_output = _lint_json([
        {"check_name": "MODULE_MISSING_VERSIONS_TOPIC", "message": "versions channel has no topic"},
        {"check_name": "MODULE_MISSING_EXT_ARGS", "message": "ext.args not set"},
    ])

    with (
        patch("code_to_module.validate.check_nf_core_available", return_value=(True, "")),
        patch("code_to_module.validate.subprocess.run") as mock_run,
    ):
        lint_proc = MagicMock()
        lint_proc.returncode = 1
        lint_proc.stdout = lint_output
        lint_proc.stderr = ""

        nftest_proc = MagicMock()
        nftest_proc.returncode = 0
        nftest_proc.stdout = "PASSED  test sort\n"
        nftest_proc.stderr = ""

        mock_run.side_effect = [lint_proc, nftest_proc]

        from code_to_module.standards.loader import Standards
        report = run_validation(tmp_path, Standards())

    assert report.lint_passed is False
    assert len(report.lint_failures) == 2
    assert all(f.fix_class == FixClass.CLASS_A for f in report.lint_failures)
    assert report.class_a_count == 2
    assert report.class_b_count == 0


def test_lint_class_b_detection(tmp_path: Path) -> None:
    """Lint failure with CLASS_B codes counted and returned correctly."""
    test_file = tmp_path / "tests" / "main.nf.test"
    test_file.parent.mkdir()
    test_file.write_text("nextflow_process {}")

    lint_output = _lint_json([
        {"check_name": "MODULE_INCORRECT_OUTPUT_PATTERN", "message": "glob pattern wrong"},
        {"check_name": "MODULE_LABEL_INAPPROPRIATE", "message": "label too high"},
    ])

    with (
        patch("code_to_module.validate.check_nf_core_available", return_value=(True, "")),
        patch("code_to_module.validate.subprocess.run") as mock_run,
    ):
        lint_proc = MagicMock()
        lint_proc.returncode = 1
        lint_proc.stdout = lint_output
        lint_proc.stderr = ""

        nftest_proc = MagicMock()
        nftest_proc.returncode = 0
        nftest_proc.stdout = ""
        nftest_proc.stderr = ""

        mock_run.side_effect = [lint_proc, nftest_proc]

        from code_to_module.standards.loader import Standards
        report = run_validation(tmp_path, Standards())

    assert report.lint_passed is False
    assert report.class_b_count == 2
    assert report.class_a_count == 0


def test_nftest_snapshot_is_class_a(tmp_path: Path) -> None:
    """nf-test snapshot mismatch → CLASS_A when .snap file exists."""
    snap_dir = tmp_path / "tests"
    snap_dir.mkdir()
    (snap_dir / "main.nf.test").write_text("nextflow_process {}")
    (snap_dir / "main.nf.test.snap").write_text("{}")

    nftest_raw = _nftest_output("test sort bam", "snapshot mismatch detected in output")

    with (
        patch("code_to_module.validate.check_nf_core_available", return_value=(True, "")),
        patch("code_to_module.validate.subprocess.run") as mock_run,
    ):
        lint_proc = MagicMock()
        lint_proc.returncode = 0
        lint_proc.stdout = _lint_json([])
        lint_proc.stderr = ""

        nftest_proc = MagicMock()
        nftest_proc.returncode = 1
        nftest_proc.stdout = nftest_raw
        nftest_proc.stderr = ""

        mock_run.side_effect = [lint_proc, nftest_proc]

        from code_to_module.standards.loader import Standards
        report = run_validation(tmp_path, Standards())

    assert report.nftest_passed is False
    snapshot_failures = [f for f in report.nftest_failures if f.fix_class == FixClass.CLASS_A]
    assert len(snapshot_failures) >= 1


def test_nftest_wrong_pattern_is_class_b(tmp_path: Path) -> None:
    """nf-test file-not-found with glob pattern → CLASS_B."""
    snap_dir = tmp_path / "tests"
    snap_dir.mkdir()
    (snap_dir / "main.nf.test").write_text("nextflow_process {}")

    nftest_raw = _nftest_output(
        "test sort bam",
        "No such file or directory: *.sorted.bam output path",
    )

    with (
        patch("code_to_module.validate.check_nf_core_available", return_value=(True, "")),
        patch("code_to_module.validate.subprocess.run") as mock_run,
    ):
        lint_proc = MagicMock()
        lint_proc.returncode = 0
        lint_proc.stdout = _lint_json([])
        lint_proc.stderr = ""

        nftest_proc = MagicMock()
        nftest_proc.returncode = 1
        nftest_proc.stdout = nftest_raw
        nftest_proc.stderr = ""

        mock_run.side_effect = [lint_proc, nftest_proc]

        from code_to_module.standards.loader import Standards
        report = run_validation(tmp_path, Standards())

    assert report.nftest_passed is False
    class_b_failures = [f for f in report.nftest_failures if f.fix_class == FixClass.CLASS_B]
    assert len(class_b_failures) >= 1


def test_class_c_default_for_unknown(tmp_path: Path) -> None:
    """Unknown lint code is classified as CLASS_C."""
    test_file = tmp_path / "tests" / "main.nf.test"
    test_file.parent.mkdir()
    test_file.write_text("nextflow_process {}")

    lint_output = _lint_json([
        {"check_name": "COMPLETELY_UNKNOWN_CHECK_XYZ", "message": "something failed"},
    ])

    with (
        patch("code_to_module.validate.check_nf_core_available", return_value=(True, "")),
        patch("code_to_module.validate.subprocess.run") as mock_run,
    ):
        lint_proc = MagicMock()
        lint_proc.returncode = 1
        lint_proc.stdout = lint_output
        lint_proc.stderr = ""

        nftest_proc = MagicMock()
        nftest_proc.returncode = 0
        nftest_proc.stdout = ""
        nftest_proc.stderr = ""

        mock_run.side_effect = [lint_proc, nftest_proc]

        from code_to_module.standards.loader import Standards
        report = run_validation(tmp_path, Standards())

    assert report.class_c_count >= 1
    class_c_codes = [f.code for f in report.lint_failures if f.fix_class == FixClass.CLASS_C]
    assert "COMPLETELY_UNKNOWN_CHECK_XYZ" in class_c_codes


def test_lint_only_skips_nftest(tmp_path: Path) -> None:
    """lint_only=True → nf-test subprocess is not called."""
    with (
        patch("code_to_module.validate.check_nf_core_available", return_value=(True, "")),
        patch("code_to_module.validate.subprocess.run") as mock_run,
    ):
        lint_proc = MagicMock()
        lint_proc.returncode = 0
        lint_proc.stdout = _lint_json([])
        lint_proc.stderr = ""

        mock_run.return_value = lint_proc

        from code_to_module.standards.loader import Standards
        report = run_validation(tmp_path, Standards(), lint_only=True)

    assert mock_run.call_count == 1
    assert report.nftest_passed is True
    assert report.nftest_failures == []


@pytest.mark.integration
def test_integration_passing_fixture() -> None:
    """Integration: real subprocess calls on passing fixture.

    Requires nf-core>=2.14 and nf-test. Skipped in standard CI.
    """
    from code_to_module.standards.loader import Standards
    report = run_validation(_PASSING, Standards())
    assert isinstance(report, TestReport)
