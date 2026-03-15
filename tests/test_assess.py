"""Tests for assess.py."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from code_to_module.assess import assess
from code_to_module.models import CodeSource, DetectionMethod, FunctionalitySpec
from code_to_module.standards import Standards

# ── Helpers ───────────────────────────────────────────────────────────────────

_TODAY = date.today().isoformat()

# Full minimal schema satisfying all _REQUIRED_SCHEMA_KEYS in loader.py
_FULL_SCHEMA: dict = {
    "schema_version": "3.5.0",
    "last_updated": _TODAY,
    "valid_labels": ["process_single", "process_medium", "process_high", "process_high_memory"],
    "label_resources": {
        "process_single": {"cpus": 1, "memory_gb": 6, "time_h": 4},
        "process_medium": {"cpus": 6, "memory_gb": 36, "time_h": 8},
        "process_high": {"cpus": 12, "memory_gb": 72, "time_h": 16},
        "process_high_memory": {"cpus": 12, "memory_gb": 200, "time_h": 16},
    },
    "versions_use_topic_channels": True,
    "docker_registry": "quay.io/biocontainers/",
    "singularity_registry": "https://depot.galaxyproject.org/singularity/",
    "conda_channels": ["conda-forge", "bioconda", "defaults"],
    "meta_yml_required_fields": ["name", "description", "tools", "input", "output"],
    "ext_args_pattern": "def args = task.ext.args ?: ''",
    "ext_prefix_pattern": "def prefix = task.ext.prefix ?: \"${meta.id}\"",
    "known_tools": [
        "samtools", "bwa", "bcftools", "fastqc", "trimmomatic",
        "hisat2", "star", "bowtie2", "gatk", "picard",
    ],
    "tier_thresholds": {
        "tier1_max": 1, "tier2_max": 2, "tier3_max": 3, "tier4_max": 4,
        "pre_select_min_confidence": 0.70,
    },
    "helper_filename_patterns": ["utils", "helpers", "common"],
    "helper_dirname_patterns": ["tests", "docs"],
    "test_data_base_path": "https://raw.githubusercontent.com/nf-core/test-datasets/",
    "test_data_index": [
        {"id": "t1", "paths": ["a.bam"], "tags": ["bam"], "organism": "human", "size_kb": 100}
    ],
    "derivation_templates": {},
    "chain_modules": {},
}


def _minimal_source(language: str = "bash") -> CodeSource:
    return CodeSource(
        source_type="file",
        language=language,
        raw_code="",
        filename="tool.py",
    )


def _make_func(
    name: str = "run",
    display_name: str = "Run",
    code: str = "",
    method: DetectionMethod = DetectionMethod.SHELL_CASE_STATEMENT,
    confidence: float = 0.90,
) -> FunctionalitySpec:
    return FunctionalitySpec(
        name=name,
        display_name=display_name,
        description="Test functionality",
        detection_method=method,
        confidence=confidence,
        code_section=code,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_tier1_known_tool() -> None:
    """code_section calls samtools once, Bioconda mock returns 200 → Tier 1."""
    func = _make_func(
        code=(
            "samtools sort -o out.bam in.bam\n"
            "echo done\n"
            "exit 0\n"
            "# step 1\n"
            "# step 2\n"
        ),
        method=DetectionMethod.SHELL_CASE_STATEMENT,
        confidence=0.92,
    )
    source = _minimal_source("bash")

    with patch("code_to_module.assess._check_bioconda", return_value=True), \
         patch("code_to_module.assess._check_biotools", return_value=False):
        tier, conf, warns = assess(func, source)

    assert tier == 1
    assert conf >= 0.90
    assert warns == []


def test_tier2_known_tool_complex() -> None:
    """Multiple output patterns → Tier 2 instead of Tier 1."""
    func = _make_func(
        code=(
            "samtools sort -o sorted.bam input.bam\n"
            "samtools flagstat -o stats.txt sorted.bam\n"
            "echo done\n"
            "exit 0\n"
            "# complete\n"
        ),
        method=DetectionMethod.SHELL_CASE_STATEMENT,
        confidence=0.88,
    )
    source = _minimal_source("bash")

    with patch("code_to_module.assess._check_bioconda", return_value=True), \
         patch("code_to_module.assess._check_biotools", return_value=False):
        tier, conf, warns = assess(func, source)

    assert tier == 2
    assert 0.75 <= conf <= 0.89
    assert any("output" in w.lower() for w in warns)


def test_tier3_custom_python() -> None:
    """Python with argparse, no known-tool call, no Bioconda → Tier 3."""
    code = (
        "import argparse\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--input', required=True)\n"
        "parser.add_argument('--output', required=True)\n"
        "args = parser.parse_args()\n"
        "process(args.input, args.output)\n"
    )
    func = _make_func(
        code=code,
        method=DetectionMethod.ARGPARSE_SUBPARSER,
        confidence=0.70,
    )
    source = _minimal_source("python")

    # No known tools in code — no HTTP calls will be made
    tier, conf, warns = assess(func, source)

    assert tier == 3
    assert 0.50 <= conf <= 0.74
    assert any("container" in w.lower() or "stub" in w.lower() for w in warns)


def test_tier4_no_structure() -> None:
    """LLM-inferred, low confidence, no argument parsing → Tier 4."""
    code = "\n".join(f"x_{i} = {i}" for i in range(10))  # 10 bare assignments
    func = _make_func(
        code=code,
        method=DetectionMethod.LLM_INFERENCE,
        confidence=0.50,
    )
    source = _minimal_source("python")

    tier, conf, warns = assess(func, source)

    assert tier == 4
    assert 0.25 <= conf <= 0.49
    assert len(warns) >= 1


def test_tier5_empty() -> None:
    """Empty code_section → Tier 5."""
    func = _make_func(code="", confidence=0.80)
    source = _minimal_source("python")

    tier, conf, warns = assess(func, source)

    assert tier == 5
    assert conf == 0.0
    assert len(warns) >= 1


def test_tier5_unknown_language() -> None:
    """Unknown language → Tier 5 regardless of code content."""
    code = "\n".join(f"line_{i} = {i}" for i in range(10))
    func = _make_func(code=code, confidence=0.80)
    source = _minimal_source("unknown")

    tier, conf, warns = assess(func, source)

    assert tier == 5
    assert conf == 0.0


def test_uses_standards_known_tools() -> None:
    """assess() reads known_tools from get_standards(), not a hardcoded list.

    Inject a Standards with empty known_tools — samtools must NOT be recognised
    as a known tool, so Tier 1 / Tier 2 must be unreachable.
    """
    mock_standards = Standards.from_dict({**_FULL_SCHEMA, "known_tools": []})

    func = _make_func(
        code=(
            "samtools sort -o out.bam in.bam\n"
            "echo done\n"
            "exit 0\n"
            "# step 1\n"
            "# step 2\n"
        ),
        method=DetectionMethod.SHELL_CASE_STATEMENT,
        confidence=0.92,
    )
    source = _minimal_source("bash")

    with patch("code_to_module.assess.get_standards", return_value=mock_standards):
        tier, _, _ = assess(func, source)

    assert tier not in (1, 2)
    assert tier in (3, 4)


def test_tier1_console_scripts_bioconda() -> None:
    """CONSOLE_SCRIPTS detection + Bioconda match uses func.name, not tools_found."""
    # Simulate a Python package entry point: code has no subprocess calls,
    # but func.name matches a Bioconda package.
    click_code = (
        "import click\n"
        "@click.command()\n"
        "@click.option('--indata')\n"
        "@click.option('--outdir')\n"
        "def main(indata, outdir):\n"
        "    pass\n"
    )
    func = _make_func(
        name="celltypist",
        code=click_code,
        method=DetectionMethod.CONSOLE_SCRIPTS,
        confidence=0.95,
    )
    source = _minimal_source("python")

    with patch("code_to_module.assess._check_bioconda", return_value=True), \
         patch("code_to_module.assess._check_biotools", return_value=False):
        tier, conf, warns = assess(func, source)

    assert tier in (1, 2)
    assert conf >= 0.75


def test_tier3_console_scripts_not_in_bioconda() -> None:
    """CONSOLE_SCRIPTS but not in Bioconda → falls through to Tier 3."""
    click_code = (
        "import click\n"
        "@click.command()\n"
        "@click.option('--indata')\n"
        "@click.option('--outdir')\n"
        "def main(indata, outdir):\n"
        "    pass\n"
    )
    func = _make_func(
        name="myprivatetool",
        code=click_code,
        method=DetectionMethod.CONSOLE_SCRIPTS,
        confidence=0.95,
    )
    source = _minimal_source("python")

    with patch("code_to_module.assess._check_bioconda", return_value=False):
        tier, conf, warns = assess(func, source)

    assert tier in (3, 4)


def test_multi_functionality_mixed() -> None:
    """Two FunctionalitySpecs from the same source → Tier 1 and Tier 4."""
    source = _minimal_source("bash")

    func_tier1 = _make_func(
        name="sort",
        display_name="Sort BAM",
        code=(
            "samtools sort -o out.bam in.bam\n"
            "echo done\n"
            "exit 0\n"
            "# step 1\n"
            "# step 2\n"
        ),
        method=DetectionMethod.SHELL_CASE_STATEMENT,
        confidence=0.92,
    )
    func_tier4 = _make_func(
        name="complex",
        display_name="Complex Processing",
        code="\n".join(f"step_{i} = {i}" for i in range(10)),
        method=DetectionMethod.LLM_INFERENCE,
        confidence=0.50,
    )

    with patch("code_to_module.assess._check_bioconda", return_value=True), \
         patch("code_to_module.assess._check_biotools", return_value=False):
        tier1, _, _ = assess(func_tier1, source)
        tier4, _, _ = assess(func_tier4, source)

    assert tier1 == 1
    assert tier4 == 4
