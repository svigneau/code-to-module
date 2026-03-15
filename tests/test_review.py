"""Tests for review.py — static analysis of nf-core module style.

All tests read from fixture files or write to tmp_path.
No subprocess calls, no mocking needed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_to_module.review import ReviewItem, ReviewReport, review_module
from code_to_module.standards.loader import Standards

# ── Fixture paths ──────────────────────────────────────────────────────────────

_FIXTURES = Path(__file__).parent / "fixtures" / "modules"
_PASSING = _FIXTURES / "passing"
_NEEDS_REVIEW = _FIXTURES / "needs_review"


# ── Test 1: passing module is submission ready ─────────────────────────────────


def test_passing_module_is_submission_ready() -> None:
    """passing/ fixture has no style errors -> submission_ready=True, error_count=0."""
    standards = Standards()
    report = review_module(_PASSING, standards)

    assert report.submission_ready is True
    assert report.error_count == 0


# ── Test 2: generic channel name is error ─────────────────────────────────────


def test_generic_channel_name_is_error(tmp_path: Path) -> None:
    """'output' as an emit name -> ERROR channel_naming."""
    (tmp_path / "main.nf").write_text(
        "process FOO {\n"
        "    label 'process_single'\n"
        "    input:\n"
        "    tuple val(meta), path(reads)\n"
        "    output:\n"
        "    tuple val(meta), path('*.bam'), emit: output\n"
        "    path 'versions.yml', emit: versions, topic: 'versions'\n"
        "    script:\n"
        '    """\n'
        "    foo $reads\n"
        "    def args = task.ext.args ?: ''\n"
        "    cat <<-END_VERSIONS > versions.yml\n"
        '    "${task.process}":\n'
        "        foo: 1.0\n"
        "    END_VERSIONS\n"
        '    """\n'
        "}\n"
    )
    standards = Standards()
    report = review_module(tmp_path, standards)

    errors = [i for i in report.items if i.severity == "ERROR" and i.category == "channel_naming"]
    assert any("output" in i.message for i in errors), report.items


# ── Test 3: missing EDAM ontology is warning ──────────────────────────────────


def test_missing_edam_ontology_is_warning(tmp_path: Path) -> None:
    """A channel with a known format (fastq) but no ontology field -> WARNING meta_yml."""
    (tmp_path / "main.nf").write_text(
        "process FOO {\n"
        "    label 'process_single'\n"
        "    input:\n"
        "    tuple val(meta), path(reads)\n"
        "    output:\n"
        "    tuple val(meta), path('*.bam'), emit: bam\n"
        "    path 'versions.yml', emit: versions, topic: 'versions'\n"
        "    script:\n"
        '    """\n'
        "    def args = task.ext.args ?: ''\n"
        "    foo\n"
        "    cat <<-END_VERSIONS > versions.yml\n"
        '    "${task.process}": {foo: 1.0}\n'
        "    END_VERSIONS\n"
        '    """\n'
        "}\n"
    )
    (tmp_path / "meta.yml").write_text(
        "name: foo/run\n"
        "description: Run foo\n"
        "keywords: [a, b, c]\n"
        "tools:\n"
        "  - foo:\n"
        "      description: Foo tool\n"
        "      homepage: https://foo.example.com\n"
        "      documentation: https://foo.example.com/docs\n"
        "input:\n"
        "  - meta:\n"
        "      type: map\n"
        "  - reads:\n"
        "      type: file\n"
        "      description: Input reads\n"
        "      pattern: '*.fastq.gz'\n"  # fastq.gz -> fastq EDAM
        "output:\n"
        "  - meta:\n"
        "      type: map\n"
        "  - bam:\n"
        "      type: file\n"
        "      description: Output BAM\n"
        "      pattern: '*.bam'\n"
        "  - versions:\n"
        "      type: file\n"
        "      description: versions\n"
        "      pattern: 'versions.yml'\n"
    )
    standards = Standards()
    report = review_module(tmp_path, standards)

    warnings = [i for i in report.items if i.severity == "WARNING" and i.category == "meta_yml"]
    edam_warnings = [i for i in warnings if "ontology" in i.message.lower() or "edam" in i.message.lower()]
    assert len(edam_warnings) >= 1, f"Expected EDAM warning, got: {report.items}"


# ── Test 4: missing homepage is error ─────────────────────────────────────────


def test_missing_homepage_is_error(tmp_path: Path) -> None:
    """Tool entry without homepage -> ERROR meta_yml."""
    (tmp_path / "main.nf").write_text(
        "process FOO {\n"
        "    label 'process_single'\n"
        "    input:\n"
        "    tuple val(meta), path(reads)\n"
        "    output:\n"
        "    tuple val(meta), path('*.bam'), emit: bam\n"
        "    path 'versions.yml', emit: versions, topic: 'versions'\n"
        "    script:\n"
        '    """\n'
        "    def args = task.ext.args ?: ''\n"
        "    foo\n"
        "    cat <<-END_VERSIONS > versions.yml\n"
        '    "${task.process}": {foo: 1.0}\n'
        "    END_VERSIONS\n"
        '    """\n'
        "}\n"
    )
    (tmp_path / "meta.yml").write_text(
        "name: foo/run\n"
        "description: Run foo\n"
        "keywords: [a, b, c]\n"
        "tools:\n"
        "  - foo:\n"
        "      description: Foo bioinformatics tool\n"
        # homepage intentionally missing
        "      documentation: https://foo.example.com/docs\n"
        "input:\n"
        "  - meta:\n"
        "      type: map\n"
        "output:\n"
        "  - meta:\n"
        "      type: map\n"
        "  - bam:\n"
        "      type: file\n"
        "      description: BAM output\n"
        "      pattern: '*.bam'\n"
        "  - versions:\n"
        "      type: file\n"
        "      description: versions\n"
        "      pattern: 'versions.yml'\n"
    )
    standards = Standards()
    report = review_module(tmp_path, standards)

    errors = [i for i in report.items if i.severity == "ERROR" and i.category == "meta_yml"]
    assert any("homepage" in i.message.lower() for i in errors), report.items


# ── Test 5: unpinned conda dependency is warning ──────────────────────────────


def test_unpinned_dependency_is_warning(tmp_path: Path) -> None:
    """'- samtools' with no version pin -> WARNING conda."""
    (tmp_path / "main.nf").write_text("process FOO {}\n")
    (tmp_path / "environment.yml").write_text(
        "name: nf-core-samtools-1.17\n"
        "channels:\n"
        "  - conda-forge\n"
        "  - bioconda\n"
        "  - defaults\n"
        "dependencies:\n"
        "  - samtools\n"
    )
    standards = Standards()
    report = review_module(tmp_path, standards)

    warnings = [i for i in report.items if i.severity == "WARNING" and i.category == "conda"]
    assert any("samtools" in i.message for i in warnings), report.items


# ── Test 6: missing task.ext.args is error ────────────────────────────────────


def test_missing_ext_args_is_error(tmp_path: Path) -> None:
    """No task.ext.args in script -> ERROR ext_args."""
    (tmp_path / "main.nf").write_text(
        "process FOO {\n"
        "    label 'process_single'\n"
        "    input:\n"
        "    tuple val(meta), path(reads)\n"
        "    output:\n"
        "    tuple val(meta), path('*.bam'), emit: bam\n"
        "    path 'versions.yml', emit: versions, topic: 'versions'\n"
        "    script:\n"
        '    """\n'
        "    foo --input $reads\n"
        "    cat <<-END_VERSIONS > versions.yml\n"
        '    "${task.process}": {foo: 1.0}\n'
        "    END_VERSIONS\n"
        '    """\n'
        "}\n"
    )
    standards = Standards()
    report = review_module(tmp_path, standards)

    errors = [i for i in report.items if i.severity == "ERROR" and i.category == "ext_args"]
    assert len(errors) >= 1, report.items
    assert any("task.ext.args" in i.message for i in errors)


# ── Test 7: heavy tool with wrong label is error ──────────────────────────────


def test_heavy_tool_wrong_label(tmp_path: Path) -> None:
    """gatk with process_single -> ERROR process_label."""
    (tmp_path / "main.nf").write_text(
        "process GATK4_HAPLOTYPECALLER {\n"
        "    label 'process_single'\n"
        "    container 'quay.io/biocontainers/gatk4:4.4.0.0--py36hdfd78af_0'\n"
        "    input:\n"
        "    tuple val(meta), path(bam)\n"
        "    output:\n"
        "    tuple val(meta), path('*.vcf.gz'), emit: vcf\n"
        "    path 'versions.yml', emit: versions, topic: 'versions'\n"
        "    script:\n"
        '    """\n'
        "    def args = task.ext.args ?: ''\n"
        "    gatk HaplotypeCaller $args -I $bam -O output.vcf.gz\n"
        "    cat <<-END_VERSIONS > versions.yml\n"
        '    "${task.process}": {gatk: 4.4}\n'
        "    END_VERSIONS\n"
        '    """\n'
        "}\n"
    )
    standards = Standards()
    report = review_module(tmp_path, standards)

    errors = [i for i in report.items if i.severity == "ERROR" and i.category == "process_label"]
    assert len(errors) >= 1, report.items
    assert any("gatk" in i.message.lower() for i in errors)


# ── Test 8: channel in main.nf not in meta.yml is error ───────────────────────


def test_channel_not_in_meta_yml_is_error(tmp_path: Path) -> None:
    """emit name 'report' in main.nf with no matching output in meta.yml -> ERROR meta_yml."""
    (tmp_path / "main.nf").write_text(
        "process FOO {\n"
        "    label 'process_single'\n"
        "    input:\n"
        "    tuple val(meta), path(reads)\n"
        "    output:\n"
        "    tuple val(meta), path('*.bam'), emit: bam\n"
        "    tuple val(meta), path('*.html'), emit: report\n"  # report not in meta.yml
        "    path 'versions.yml', emit: versions, topic: 'versions'\n"
        "    script:\n"
        '    """\n'
        "    def args = task.ext.args ?: ''\n"
        "    foo\n"
        "    cat <<-END_VERSIONS > versions.yml\n"
        '    "${task.process}": {foo: 1.0}\n'
        "    END_VERSIONS\n"
        '    """\n'
        "}\n"
    )
    (tmp_path / "meta.yml").write_text(
        "name: foo/run\n"
        "description: Run foo\n"
        "keywords: [a, b, c]\n"
        "tools:\n"
        "  - foo:\n"
        "      description: Foo tool\n"
        "      homepage: https://foo.example.com\n"
        "      documentation: https://foo.example.com/docs\n"
        "input:\n"
        "  - meta:\n"
        "      type: map\n"
        "output:\n"
        "  - meta:\n"
        "      type: map\n"
        "  - bam:\n"
        "      type: file\n"
        "      description: BAM output\n"
        "      pattern: '*.bam'\n"
        # 'report' is deliberately missing
        "  - versions:\n"
        "      type: file\n"
        "      description: versions\n"
        "      pattern: 'versions.yml'\n"
    )
    standards = Standards()
    report = review_module(tmp_path, standards)

    errors = [i for i in report.items if i.severity == "ERROR" and i.category == "meta_yml"]
    assert any("report" in i.message for i in errors), report.items


# ── Test 9: --errors-only flag filters warnings and info ──────────────────────


def test_errors_only_flag(tmp_path: Path) -> None:
    """errors_only=True -> ReviewReport.items still contains all items,
    but print_report only shows ERROR severity (tested via the CLI).
    The report object itself is unfiltered; the CLI does the filtering.
    This test verifies the CLI --errors-only flag works end-to-end."""
    from click.testing import CliRunner

    from code_to_module.validate_cli import main as cli_main

    # needs_review fixture has both warnings and errors; use a minimal tmp module
    (tmp_path / "main.nf").write_text(
        "process FOO {\n"
        "    label 'process_single'\n"
        "    input:\n"
        "    tuple val(meta), path(reads)\n"
        "    output:\n"
        "    tuple val(meta), path('*.bam'), emit: output\n"  # generic name -> ERROR
        "    path 'versions.yml', emit: versions, topic: 'versions'\n"
        "    script:\n"
        '    """\n'
        "    def args = task.ext.args ?: ''\n"
        "    foo\n"
        "    cat <<-END_VERSIONS > versions.yml\n"
        '    "${task.process}": {foo: 1.0}\n'
        "    END_VERSIONS\n"
        '    """\n'
        "}\n"
    )
    (tmp_path / "environment.yml").write_text(
        "name: nf-core-foo-1.0\n"
        "channels:\n  - conda-forge\n  - bioconda\n  - defaults\n"
        "dependencies:\n  - foo\n"  # unpinned -> WARNING
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["review", str(tmp_path), "--errors-only"])

    # Should show ERROR items but not WARNING/INFO in output
    assert "ERROR" in result.output or "✗" in result.output or "generic" in result.output.lower()
    # INFO-level conda name mismatch should NOT appear in --errors-only output
    assert "Convention: environment name" not in result.output


# ── Test 10: --json-output flag writes valid ReviewReport JSON ─────────────────


def test_json_output(tmp_path: Path) -> None:
    """--json-output writes a valid ReviewReport JSON file."""
    from click.testing import CliRunner

    from code_to_module.validate_cli import main as cli_main

    (tmp_path / "main.nf").write_text(
        "process FOO {\n"
        "    label 'process_single'\n"
        "    input:\n"
        "    tuple val(meta), path(reads)\n"
        "    output:\n"
        "    tuple val(meta), path('*.bam'), emit: bam\n"
        "    path 'versions.yml', emit: versions, topic: 'versions'\n"
        "    script:\n"
        '    """\n'
        "    def args = task.ext.args ?: ''\n"
        "    foo\n"
        "    cat <<-END_VERSIONS > versions.yml\n"
        '    "${task.process}": {foo: 1.0}\n'
        "    END_VERSIONS\n"
        '    """\n'
        "}\n"
    )

    out_path = tmp_path / "review_report.json"
    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        ["review", str(tmp_path), "--json-output", str(out_path)],
    )

    assert out_path.exists(), result.output
    data = json.loads(out_path.read_text())
    assert "submission_ready" in data
    assert "error_count" in data
    assert "items" in data
    # Validate it round-trips through the model
    report = ReviewReport.model_validate(data)
    assert isinstance(report.submission_ready, bool)
