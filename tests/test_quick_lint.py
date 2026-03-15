"""Tests for quick_lint.py — fast post-generate structural checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from code_to_module.cli import main
from code_to_module.quick_lint import quick_lint
from code_to_module.standards.loader import Standards

# ── Helpers ───────────────────────────────────────────────────────────────────

_VALID_MAIN_NF = """\
process MYTOOL_RUN {
    label 'process_single'
    container 'quay.io/biocontainers/mytool:1.0.0--pyhdfd78af_0'

    input:
    tuple val(meta), path(reads)

    output:
    tuple val(meta), path("*.bam"), emit: bam
    path "versions.yml",            emit: versions, topic: 'versions'

    script:
    def args = task.ext.args ?: ''
    def prefix = task.ext.prefix ?: "${meta.id}"
    \"\"\"
    mytool \\
        $args \\
        --output ${prefix}.bam \\
        $reads
    \"\"\"
}
"""

_VALID_META_YML = """\
name: mytool/run
description: Runs mytool
tools:
  - mytool:
      description: A tool
      homepage: https://example.com
      documentation: https://example.com/docs
      licence: ['MIT']
input:
  - meta:
      type: map
  - reads:
      type: file
output:
  - bam:
      type: file
"""


def _make_standards() -> Standards:
    """Return Standards loaded from the bundled schema."""
    return Standards()


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_passes_clean_module(tmp_path: Path) -> None:
    """Valid main.nf with all required elements → empty list."""
    (tmp_path / "main.nf").write_text(_VALID_MAIN_NF)
    (tmp_path / "meta.yml").write_text(_VALID_META_YML)

    standards = _make_standards()
    issues = quick_lint(tmp_path, standards)

    assert issues == [], f"Expected no issues, got: {issues}"


def test_missing_container(tmp_path: Path) -> None:
    """main.nf with 'container TODO' → QuickLintWarning severity=error, check=missing_container."""
    content = _VALID_MAIN_NF.replace(
        "container 'quay.io/biocontainers/mytool:1.0.0--pyhdfd78af_0'",
        "container 'TODO'",
    )
    (tmp_path / "main.nf").write_text(content)
    (tmp_path / "meta.yml").write_text(_VALID_META_YML)

    standards = _make_standards()
    issues = quick_lint(tmp_path, standards)

    container_issues = [i for i in issues if i.check == "missing_container"]
    assert len(container_issues) == 1
    assert container_issues[0].severity == "error"


def test_missing_ext_args(tmp_path: Path) -> None:
    """No task.ext.args in script: block → error."""
    content = _VALID_MAIN_NF.replace("    def args = task.ext.args ?: ''\n", "")
    (tmp_path / "main.nf").write_text(content)
    (tmp_path / "meta.yml").write_text(_VALID_META_YML)

    standards = _make_standards()
    issues = quick_lint(tmp_path, standards)

    ext_args_issues = [i for i in issues if i.check == "missing_ext_args"]
    assert len(ext_args_issues) == 1
    assert ext_args_issues[0].severity == "error"
    # Suggestion should reference the expected pattern from standards
    assert standards.ext_args_pattern in ext_args_issues[0].suggestion


def test_missing_ext_prefix_with_outputs(tmp_path: Path) -> None:
    """File outputs present but no task.ext.prefix → warning."""
    content = _VALID_MAIN_NF.replace('    def prefix = task.ext.prefix ?: "${meta.id}"\n', "")
    (tmp_path / "main.nf").write_text(content)
    (tmp_path / "meta.yml").write_text(_VALID_META_YML)

    standards = _make_standards()
    issues = quick_lint(tmp_path, standards)

    prefix_issues = [i for i in issues if i.check == "missing_ext_prefix"]
    assert len(prefix_issues) == 1
    assert prefix_issues[0].severity == "warning"


def test_wrong_label(tmp_path: Path) -> None:
    """label 'process_extreme' → error with valid labels in suggestion."""
    content = _VALID_MAIN_NF.replace("label 'process_single'", "label 'process_extreme'")
    (tmp_path / "main.nf").write_text(content)
    (tmp_path / "meta.yml").write_text(_VALID_META_YML)

    standards = _make_standards()
    issues = quick_lint(tmp_path, standards)

    label_issues = [i for i in issues if i.check == "wrong_label"]
    assert len(label_issues) == 1
    assert label_issues[0].severity == "error"
    for valid_label in standards.valid_labels:
        assert valid_label in label_issues[0].suggestion


def test_missing_versions_topic(tmp_path: Path) -> None:
    """No 'topic: versions' in main.nf → error (when standards.versions_use_topic_channels is True)."""
    content = _VALID_MAIN_NF.replace(", topic: 'versions'", "")
    (tmp_path / "main.nf").write_text(content)
    (tmp_path / "meta.yml").write_text(_VALID_META_YML)

    standards = _make_standards()
    assert standards.versions_use_topic_channels, "Schema must require topic channels for this test"

    issues = quick_lint(tmp_path, standards)

    topic_issues = [i for i in issues if i.check == "missing_versions_topic"]
    assert len(topic_issues) == 1
    assert topic_issues[0].severity == "error"


def test_meta_yml_missing_field(tmp_path: Path) -> None:
    """meta.yml without 'tools' key → warning for that field."""
    # Remove the 'tools:' section by only keeping non-tools lines
    meta_lines = [
        line for line in _VALID_META_YML.splitlines()
        if not (
            line.startswith("tools")
            or line.startswith("  - mytool")
            or "description: A tool" in line
            or "homepage:" in line
            or "documentation:" in line
            or "licence:" in line
        )
    ]
    meta_without_tools = "\n".join(meta_lines)
    (tmp_path / "main.nf").write_text(_VALID_MAIN_NF)
    (tmp_path / "meta.yml").write_text(meta_without_tools)

    standards = _make_standards()
    issues = quick_lint(tmp_path, standards)

    meta_issues = [i for i in issues if i.check == "meta_yml_missing_field"]
    assert len(meta_issues) >= 1
    assert any("tools" in i.message for i in meta_issues)
    assert all(i.severity == "warning" for i in meta_issues)


def test_no_lint_flag(tmp_path: Path) -> None:
    """--no-lint passes no_lint=True to api.convert() and skips quick_lint."""
    from code_to_module.models import CodeSource

    runner = CliRunner()

    script = tmp_path / "script.py"
    script.write_text("print('hello')")
    source = CodeSource(
        source_type="file",
        path=script,
        language="python",
        raw_code="print('hello')",
        filename="script.py",
    )

    api_result = {
        "success": True,
        "modules": [
            {
                "functionality_name": "run",
                "process_name": "RUN",
                "tier": 1,
                "confidence": 0.9,
                "container_source": "biocontainers",
                "container_docker": "quay.io/biocontainers/mytool:1.0.0",
                "test_data_strategies": {},
                "needs_derivation": False,
                "files_created": [str(tmp_path / "main.nf")],
                "warnings": [],
                "module_spec": {"tool_name": "mytool"},
            }
        ],
        "functionalities_found": ["run"],
        "functionalities_selected": ["run"],
        "detection_method": "single_script",
        "error": None,
    }

    with (
        patch("code_to_module.ingest.ingest", return_value=source),
        patch("code_to_module.api.convert", return_value=api_result) as mock_api,
        patch("code_to_module.api.quick_lint") as mock_lint,
    ):
        result = runner.invoke(main, [
            "convert", str(script), "--outdir", str(tmp_path / "out"),
            "--no-interaction", "--no-lint",
        ])

    assert result.exit_code == 0, result.output
    # api.convert should have been called with no_lint=True
    call_kwargs = mock_api.call_args
    assert call_kwargs.kwargs.get("no_lint") is True
    # quick_lint inside api is not reached when api.convert is mocked — that's correct
    mock_lint.assert_not_called()
