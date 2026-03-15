"""Tests for fix.py — rule-based and LLM-assisted fix proposal + application.

All Anthropic API calls and nf-core/nf-test subprocess calls are mocked.
File writes use tmp_path — real fixture files are never modified.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

from code_to_module.fix import (
    FixSource,
    ProposedFix,
    _rule_missing_topic,
    _rule_stale_snapshot,
    _rule_wrong_container_prefix,
    apply_fix,
)
from code_to_module.standards.loader import Standards
from code_to_module.validate import FixClass, LintFailure, NfTestFailure, TestReport

# ── Fixture paths ──────────────────────────────────────────────────────────────

_FIXTURES = Path(__file__).parent / "fixtures" / "modules"
_FAILING_LINT = _FIXTURES / "failing_lint"
_FAILING_NFTEST = _FIXTURES / "failing_nftest"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _copy_fixture(src: Path, dst: Path) -> None:
    """Copy an entire fixture directory into tmp_path for isolated writes."""
    if src.exists():
        shutil.copytree(src, dst)


def _make_report(
    module_path: Path,
    lint_failures: list[LintFailure] | None = None,
    nftest_failures: list[NfTestFailure] | None = None,
) -> TestReport:
    all_lint = lint_failures or []
    all_nft = nftest_failures or []
    class_a = sum(1 for f in all_lint + all_nft if f.fix_class == FixClass.CLASS_A)  # type: ignore[operator]
    class_b = sum(1 for f in all_lint + all_nft if f.fix_class == FixClass.CLASS_B)  # type: ignore[operator]
    class_c = sum(1 for f in all_lint + all_nft if f.fix_class == FixClass.CLASS_C)  # type: ignore[operator]
    return TestReport(
        module_path=str(module_path),
        lint_passed=len(all_lint) == 0,
        nftest_passed=len(all_nft) == 0,
        lint_failures=all_lint,
        nftest_failures=all_nft,
        class_a_count=class_a,
        class_b_count=class_b,
        class_c_count=class_c,
    )


# ── Test 1: missing topic channel rule ────────────────────────────────────────


def test_rule_missing_topic(tmp_path: Path) -> None:
    """LintFailure MODULE_MISSING_VERSIONS_TOPIC -> ProposedFix with RULE source,
    unified diff contains 'topic: versions'.
    """
    _copy_fixture(_FAILING_LINT, tmp_path / "module")
    module_path = tmp_path / "module"
    standards = Standards()

    failure = LintFailure(
        code="MODULE_MISSING_VERSIONS_TOPIC",
        message="versions channel has no topic",
        fix_class=FixClass.CLASS_A,
    )
    fix = _rule_missing_topic(failure, module_path, standards)

    assert fix is not None
    assert fix.fix_source == FixSource.RULE
    assert fix.fix_class == FixClass.CLASS_A
    assert "topic" in fix.diff and "versions" in fix.diff
    assert fix.new_file_content != ""
    assert "topic: 'versions'" in fix.new_file_content


# ── Test 2: wrong container prefix rule ──────────────────────────────────────


def test_rule_wrong_container_prefix(tmp_path: Path) -> None:
    """Wrong docker.io prefix -> ProposedFix diff replaces docker.io with quay.io."""
    module_path = tmp_path / "module"
    module_path.mkdir()
    (module_path / "main.nf").write_text(
        'container "docker.io/biocontainers/samtools:1.17--h00cdaf9_0"\n'
        'label "process_single"\n'
        'script:\n"""\nsamtools sort\n"""\n'
    )

    standards = Standards()
    failure = LintFailure(
        code="MODULE_CONTAINER_URL_FORMAT",
        message="container URL should use quay.io",
        fix_class=FixClass.CLASS_A,
    )
    fix = _rule_wrong_container_prefix(failure, module_path, standards)

    assert fix is not None
    assert fix.fix_source == FixSource.RULE
    assert "docker.io/biocontainers/" in fix.diff
    assert "quay.io/biocontainers/" in fix.diff
    assert "quay.io/biocontainers/" in fix.new_file_content
    assert "docker.io/biocontainers/" not in fix.new_file_content


# ── Test 3: stale snapshot rule ───────────────────────────────────────────────


def test_rule_stale_snapshot(tmp_path: Path) -> None:
    """NfTestFailure snapshot_mismatch -> ProposedFix describes deletion (is_deletion=True)."""
    _copy_fixture(_FAILING_NFTEST, tmp_path / "module")
    module_path = tmp_path / "module"
    standards = Standards()

    nft_failure = NfTestFailure(
        test_name="test sort bam",
        error_type="snapshot_mismatch",
        error="snapshot mismatch detected",
        fix_class=FixClass.CLASS_A,
    )
    fix = _rule_stale_snapshot(nft_failure, module_path, standards)

    assert fix is not None
    assert fix.fix_source == FixSource.RULE
    assert fix.is_deletion is True
    assert fix.fix_class == FixClass.CLASS_A
    assert fix.file_path.endswith(".snap")


# ── Test 4: LLM fix - output pattern (valid diff) ────────────────────────────


def test_llm_fix_output_pattern(tmp_path: Path) -> None:
    """Mock Anthropic API returning valid diff -> ProposedFix with LLM source, approved=True."""
    module_path = tmp_path / "module"
    module_path.mkdir()
    main_nf_content = (
        "process MYTOOL_RUN {\n"
        "    label 'process_single'\n"
        "    output:\n"
        "    tuple val(meta), path('*.bam'),  emit: bam\n"
        "    script:\n"
        '    """\n'
        "    mytool --output ${prefix}.sorted.bam input.bam\n"
        '    """\n'
        "}\n"
    )
    (module_path / "main.nf").write_text(main_nf_content)

    valid_diff = (
        "--- main.nf\n"
        "+++ main.nf\n"
        "@@ -4,1 +4,1 @@\n"
        "-    tuple val(meta), path('*.bam'),  emit: bam\n"
        "+    tuple val(meta), path('${prefix}.sorted.bam'),  emit: bam\n"
    )

    failure = NfTestFailure(
        test_name="test run",
        error_type="file_not_found",
        error="No such file: *.bam output",
        fix_class=FixClass.CLASS_B,
    )

    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=valid_diff)]

    with patch("code_to_module.fix.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_msg
        standards = Standards()
        from code_to_module.fix import _llm_fix_output_pattern

        fix = _llm_fix_output_pattern(failure, module_path, standards)

    assert fix.fix_source == FixSource.LLM
    assert fix.fix_class == FixClass.CLASS_B
    assert fix.approved is True
    assert "${prefix}.sorted.bam" in fix.new_file_content


# ── Test 5: LLM fix - unparseable response -> CLASS_C degradation ─────────────


def test_llm_fix_unparseable_response(tmp_path: Path) -> None:
    """Mock API returning prose instead of a diff -> approved=False, CLASS_C."""
    module_path = tmp_path / "module"
    module_path.mkdir()
    (module_path / "main.nf").write_text(
        "process MYTOOL_RUN {\n"
        "    output:\n"
        "    tuple val(meta), path('*.bam'), emit: bam\n"
        "}\n"
    )

    prose_response = (
        "You should change the pattern from *.bam to ${prefix}.bam because "
        "the tool outputs files with the sample prefix."
    )

    failure = NfTestFailure(
        test_name="test run",
        error_type="file_not_found",
        error="No such file: *.bam",
        fix_class=FixClass.CLASS_B,
    )

    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=prose_response)]

    with patch("code_to_module.fix.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_msg
        standards = Standards()
        from code_to_module.fix import _llm_fix_output_pattern

        fix = _llm_fix_output_pattern(failure, module_path, standards)

    assert fix.approved is False
    assert fix.fix_class == FixClass.CLASS_C


# ── Test 6: apply_fix writes file ─────────────────────────────────────────────


def test_apply_fix_writes_file(tmp_path: Path) -> None:
    """apply_fix() on a rule fix -> file contents match expected new_file_content."""
    target = tmp_path / "main.nf"
    original = "path 'versions.yml', emit: versions\n"
    target.write_text(original)

    new_content = "path 'versions.yml', emit: versions, topic: 'versions'\n"
    fix = ProposedFix(
        fix_source=FixSource.RULE,
        fix_class=FixClass.CLASS_A,
        description="Add topic: 'versions'",
        file_path=str(target),
        diff="--- main.nf\n+++ main.nf\n",
        approved=True,
        new_file_content=new_content,
    )

    result = apply_fix(fix)

    assert result is True
    assert target.read_text() == new_content


# ── Test 7: apply_fix does not write unapproved fix ───────────────────────────


def test_apply_fix_does_not_write_unapproved(tmp_path: Path) -> None:
    """approved=False -> apply_fix returns False and file is unchanged."""
    target = tmp_path / "main.nf"
    original = "original content\n"
    target.write_text(original)

    fix = ProposedFix(
        fix_source=FixSource.RULE,
        fix_class=FixClass.CLASS_A,
        description="some fix",
        file_path=str(target),
        diff="",
        approved=False,
        new_file_content="changed content\n",
    )

    result = apply_fix(fix)

    assert result is False
    assert target.read_text() == original


# ── Test 8: --yes flag auto-approves only Class A ─────────────────────────────


def test_yes_flag_approves_only_class_a(tmp_path: Path) -> None:
    """--yes auto-approves Class A fixes; Class B fix still requires interaction."""
    from click.testing import CliRunner

    from code_to_module.validate_cli import main as cli_main

    module_path = tmp_path / "module"
    module_path.mkdir()
    (module_path / "main.nf").write_text("process X {}\n")
    (module_path / "tests").mkdir()
    (module_path / "tests" / "main.nf.test").write_text("")

    class_a_fix = ProposedFix(
        fix_source=FixSource.RULE,
        fix_class=FixClass.CLASS_A,
        description="Class A rule fix",
        file_path=str(module_path / "main.nf"),
        diff="--- main.nf\n+++ main.nf\n@@ -1,1 +1,1 @@\n-process X {}\n+process X { }\n",
        approved=True,
        new_file_content="process X { }\n",
    )
    class_b_fix = ProposedFix(
        fix_source=FixSource.LLM,
        fix_class=FixClass.CLASS_B,
        description="Class B LLM fix",
        file_path=str(module_path / "main.nf"),
        diff="--- main.nf\n+++ main.nf\n@@ -1,1 +1,1 @@\n-process X { }\n+process X {  }\n",
        approved=False,
        new_file_content="process X {  }\n",
        line_start=10,
        line_end=10,
    )

    runner = CliRunner()
    with (
        patch("code_to_module.validate_cli.run_validation") as mock_validate,
        patch("code_to_module.validate_cli.propose_fixes", return_value=[class_a_fix, class_b_fix]),
        patch("code_to_module.validate_cli.apply_approved_fixes", return_value=1) as mock_apply,
    ):
        from code_to_module.validate import TestReport

        mock_validate.return_value = TestReport(
            module_path=str(module_path),
            lint_passed=False,
            nftest_passed=True,
            lint_failures=[
                LintFailure(
                    code="MODULE_MISSING_VERSIONS_TOPIC",
                    message="missing topic",
                    fix_class=FixClass.CLASS_A,
                )
            ],
            class_a_count=1,
            class_b_count=1,
        )

        result = runner.invoke(
            cli_main,
            ["fix", str(module_path), "--yes", "--no-revalidate"],
            input="Y\n",
        )

    assert result.exit_code == 0, result.output
    mock_apply.assert_called_once()
    applied_fixes = mock_apply.call_args[0][0]
    class_a_fixes = [f for f in applied_fixes if f.fix_class == FixClass.CLASS_A]
    assert all(f.approved is True for f in class_a_fixes)


# ── Test 9: re-validation runs after applying fixes ───────────────────────────


def test_revalidation_after_fix(tmp_path: Path) -> None:
    """After applying fixes, run_validation is called again (twice total)."""
    from click.testing import CliRunner

    from code_to_module.validate_cli import main as cli_main

    module_path = tmp_path / "module"
    module_path.mkdir()
    (module_path / "main.nf").write_text("process X {}\n")
    (module_path / "tests").mkdir()
    (module_path / "tests" / "main.nf.test").write_text("")

    fix = ProposedFix(
        fix_source=FixSource.RULE,
        fix_class=FixClass.CLASS_A,
        description="Class A rule fix",
        file_path=str(module_path / "main.nf"),
        diff="--- main.nf\n+++ main.nf\n",
        approved=True,
        new_file_content="process X { }\n",
    )

    runner = CliRunner()
    with (
        patch("code_to_module.validate_cli.run_validation") as mock_validate,
        patch("code_to_module.validate_cli.propose_fixes", return_value=[fix]),
        patch("code_to_module.validate_cli.apply_approved_fixes", return_value=1),
    ):
        from code_to_module.validate import TestReport

        passing_report = TestReport(module_path=str(module_path))
        failing_report = TestReport(
            module_path=str(module_path),
            lint_passed=False,
            lint_failures=[
                LintFailure(
                    code="MODULE_MISSING_VERSIONS_TOPIC",
                    message="missing topic",
                    fix_class=FixClass.CLASS_A,
                )
            ],
            class_a_count=1,
        )
        mock_validate.side_effect = [failing_report, passing_report]

        result = runner.invoke(
            cli_main,
            ["fix", str(module_path), "--yes"],
            input="",
        )

    assert mock_validate.call_count == 2, result.output
