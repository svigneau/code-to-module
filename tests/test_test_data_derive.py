"""Tests for test_data_derive.py and test_gen.py orchestrator."""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from code_to_module.models import (
    ChannelSpec,
    CodeSource,
    ContainerSource,
    ModuleSpec,
    TestDataSource,
)
from code_to_module.standards import Standards
from code_to_module.test_data_derive import plan_derivation
from code_to_module.test_gen import generate_test_spec

# ── Minimal schema for mock Standards ─────────────────────────────────────────

_BASE_SCHEMA: dict = {
    "schema_version": "3.5.0",
    "last_updated": "2025-01-15",
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
    "known_tools": ["samtools", "bwa", "bcftools"],
    "tier_thresholds": {
        "tier1_max": 1,
        "tier2_max": 2,
        "tier3_max": 3,
        "tier4_max": 4,
        "pre_select_min_confidence": 0.70,
    },
    "helper_filename_patterns": ["utils", "helpers"],
    "helper_dirname_patterns": ["tests", "docs"],
    "test_data_base_path": "https://raw.githubusercontent.com/nf-core/test-datasets/",
    "test_data_index": [
        {
            "id": "sarscov2_fastq_paired",
            "paths": [
                "genomics/sarscov2/illumina/fastq/test_1.fastq.gz",
                "genomics/sarscov2/illumina/fastq/test_2.fastq.gz",
            ],
            "tags": ["fastq", "paired_end", "illumina", "short_read"],
            "organism": "sarscov2",
            "size_kb": 90,
        },
        {
            "id": "sarscov2_vcf",
            "paths": [
                "genomics/sarscov2/illumina/vcf/test.vcf.gz",
                "genomics/sarscov2/illumina/vcf/test.vcf.gz.tbi",
            ],
            "tags": ["vcf", "vcf_gz", "tbi"],
            "organism": "sarscov2",
            "size_kb": 15,
        },
        {
            "id": "sarscov2_bam",
            "paths": [
                "genomics/sarscov2/illumina/bam/test.paired_end.bam",
                "genomics/sarscov2/illumina/bam/test.paired_end.bam.bai",
            ],
            "tags": ["bam", "bai", "sorted", "illumina"],
            "organism": "sarscov2",
            "size_kb": 120,
        },
    ],
    "derivation_templates": {
        "derive_vcf": {
            "template": "derive_vcf.sh.j2",
            "tool_requirements": ["bcftools"],
            "applicable_tags": ["vcf", "vcf_gz", "bcf"],
            "source_id": "sarscov2_vcf",
            "output_pattern": "{sample_id}.vcf.gz",
        },
        "derive_bam": {
            "template": "derive_bam.sh.j2",
            "tool_requirements": ["samtools"],
            "applicable_tags": ["bam", "cram", "sam", "sorted_bam"],
            "source_id": "sarscov2_bam",
            "output_pattern": "{sample_id}_sorted.bam",
        },
    },
    "chain_modules": {
        "samtools_sort": {
            "module": "modules/nf-core/samtools/sort/main.nf",
            "process_name": "SAMTOOLS_SORT",
            "test_input_path": "genomics/sarscov2/illumina/bam/test.bam",
            "output_channel": "bam",
            "produces_tags": ["bam", "sorted_bam"],
            "fast": False,
        },
        "samtools_faidx": {
            "module": "modules/nf-core/samtools/faidx/main.nf",
            "process_name": "SAMTOOLS_FAIDX",
            "test_input_path": "genomics/sarscov2/genome/genome.fasta",
            "output_channel": "fai",
            "produces_tags": ["fasta_index", "fai"],
            "fast": True,
        },
        "bwa_index": {
            "module": "modules/nf-core/bwa/index/main.nf",
            "process_name": "BWA_INDEX",
            "test_input_path": "genomics/sarscov2/genome/genome.fasta",
            "output_channel": "index",
            "produces_tags": ["bwa_index", "genome_index"],
            "fast": True,
        },
    },
}


def _standards() -> Standards:
    return Standards.from_dict(_BASE_SCHEMA)


def _standards_no_vcf() -> Standards:
    """Standards without a VCF test_data_index entry, forcing derive/stub for vcf_gz."""
    schema = {
        **_BASE_SCHEMA,
        "test_data_index": [
            e for e in _BASE_SCHEMA["test_data_index"] if "vcf" not in e["id"]
        ],
    }
    return Standards.from_dict(schema)


def _channel(
    name: str,
    format_tags: list[str],
    strategy: str = "standard_format",
) -> ChannelSpec:
    return ChannelSpec(
        name=name,
        type="file",
        description=f"{name} channel",
        format_tags=format_tags,
        test_data_strategy=strategy,  # type: ignore[arg-type]
    )


def _minimal_spec(inputs: list[ChannelSpec]) -> ModuleSpec:
    return ModuleSpec(
        tool_name="mytool",
        process_name="MYTOOL_RUN",
        functionality_name="run",
        inputs=inputs,
        outputs=[ChannelSpec(name="out", type="file", description="output")],
        container_docker="quay.io/biocontainers/mytool:1.0.0--pyhdfd78af_0",
        container_singularity="https://depot.galaxyproject.org/singularity/mytool:1.0.0",
        container_source=ContainerSource.BIOCONTAINERS,
        label="process_medium",
        ext_args="",
        tier=1,
        confidence=0.90,
    )


def _minimal_source() -> CodeSource:
    return CodeSource(
        source_type="file",
        path=Path("/fake/script.py"),
        language="python",
        raw_code="print('hello')",
        filename="script.py",
    )


def _capture_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, no_color=True, highlight=False)
    return console, buf


# ── plan_derivation tests ──────────────────────────────────────────────────────


def test_vcf_derives() -> None:
    """format_tags=['vcf_gz'] + generatable strategy -> DERIVE with derive_vcf.sh.j2."""
    ch = _channel("vcf", ["vcf_gz"], strategy="generatable")
    plan = plan_derivation(ch, _standards(), "mytool")

    assert plan.strategy == "derive"
    assert plan.template_name == "derive_vcf.sh.j2"
    assert plan.source == TestDataSource.DERIVED
    assert "source_vcf" in plan.template_vars
    assert plan.output_files == ["mytool.vcf.gz"]
    assert "bcftools" in plan.tool_requirements
    assert plan.pr_instructions is not None


def test_sorted_bam_derives() -> None:
    """format_tags=['sorted_bam'] -> DERIVE via derive_bam template (DERIVE > CHAIN)."""
    ch = _channel("bam", ["sorted_bam"], strategy="generatable")
    plan = plan_derivation(ch, _standards(), "mytool")

    assert plan.strategy == "derive"
    assert plan.template_name == "derive_bam.sh.j2"
    assert plan.source == TestDataSource.DERIVED
    assert plan.output_files == ["mytool_sorted.bam"]


def test_bam_derives_not_chains() -> None:
    """format_tags=['bam'] matches both derive_bam and samtools_sort -> derive wins."""
    ch = _channel("bam", ["bam"], strategy="generatable")
    plan = plan_derivation(ch, _standards(), "mytool")

    assert plan.strategy == "derive"


def test_fai_chains_not_derives() -> None:
    """format_tags=['fai'] has no derive template -> CHAIN via samtools_faidx (fast=True)."""
    ch = _channel("fai", ["fai"], strategy="generatable")
    plan = plan_derivation(ch, _standards(), "mytool")

    assert plan.strategy == "chain"
    assert plan.setup_module is not None
    assert "samtools/faidx" in plan.setup_module
    assert plan.setup_process_name == "SAMTOOLS_FAIDX"
    assert plan.source == TestDataSource.CHAINED
    assert plan.output_files == []


def test_custom_is_stub() -> None:
    """channel.test_data_strategy='custom' forces STUB even when a template matches."""
    ch = _channel("vcf", ["vcf_gz"], strategy="custom")
    plan = plan_derivation(ch, _standards(), "mytool")

    assert plan.strategy == "stub"
    assert plan.source == TestDataSource.STUB


def test_unknown_format_is_stub() -> None:
    """format_tags=['count_matrix'] has no matching chain or template -> STUB."""
    ch = _channel("matrix", ["count_matrix"])
    plan = plan_derivation(ch, _standards(), "mytool")

    assert plan.strategy == "stub"
    assert plan.source == TestDataSource.STUB


# ── derivation script content tests ───────────────────────────────────────────


def test_rendered_derive_script_starts_with_shebang() -> None:
    """derive_test_data.sh content must start with '#!/usr/bin/env bash'."""
    ch = _channel("vcf", ["vcf_gz"], strategy="generatable")
    spec = _minimal_spec([ch])
    console, _ = _capture_console()

    # Use standards without VCF in index so match() misses and derive is triggered
    ts = generate_test_spec(spec, _minimal_source(), _standards_no_vcf(), console=console)

    assert ts.needs_derivation is True
    assert ts.derivation_script_content is not None
    assert ts.derivation_script_content.startswith("#!/usr/bin/env bash")


def test_rendered_derive_script_has_pr_notice() -> None:
    """derive_test_data.sh must contain the nf-core/test-datasets PR reference."""
    ch = _channel("vcf", ["vcf_gz"], strategy="generatable")
    spec = _minimal_spec([ch])
    console, _ = _capture_console()

    ts = generate_test_spec(spec, _minimal_source(), _standards_no_vcf(), console=console)

    assert ts.derivation_script_content is not None
    assert "nf-core/test-datasets" in ts.derivation_script_content


# ── orchestrator tests ─────────────────────────────────────────────────────────


def test_orchestrator_all_match() -> None:
    """All channels Strategy-1-matched -> needs_derivation=False, no script."""
    ch = _channel("reads", ["fastq", "paired_end"])
    spec = _minimal_spec([ch])
    console, _ = _capture_console()

    ts = generate_test_spec(spec, _minimal_source(), _standards(), console=console)

    assert ts.needs_derivation is False
    assert ts.derivation_script_content is None
    assert len(ts.test_cases) == 2


def test_orchestrator_chain() -> None:
    """Chain strategy (fast=True) -> TestCase.setup_module populated; needs_derivation=False."""
    ch = _channel("fai", ["fai"])
    spec = _minimal_spec([ch])
    console, _ = _capture_console()

    ts = generate_test_spec(spec, _minimal_source(), _standards(), console=console)

    real_case = next(tc for tc in ts.test_cases if not tc.is_stub_test)
    assert real_case.setup_module is not None
    assert "samtools/faidx" in real_case.setup_module
    assert ts.needs_derivation is False
    assert ts.derivation_script_content is None


def test_orchestrator_derive() -> None:
    """Derive strategy -> needs_derivation=True, script not None, PR notice present."""
    ch = _channel("vcf", ["vcf_gz"], strategy="generatable")
    spec = _minimal_spec([ch])
    console, _ = _capture_console()

    ts = generate_test_spec(spec, _minimal_source(), _standards_no_vcf(), console=console)

    assert ts.needs_derivation is True
    assert ts.derivation_script_content is not None
    assert "nf-core/test-datasets" in ts.derivation_script_content


def test_orchestrator_stub_channel() -> None:
    """Stub channel -> real TestCase has empty input_files and is_stub_test=False."""
    ch = _channel("matrix", ["count_matrix"])
    spec = _minimal_spec([ch])
    console, _ = _capture_console()

    ts = generate_test_spec(spec, _minimal_source(), _standards(), console=console)

    real_case = next(tc for tc in ts.test_cases if not tc.is_stub_test)
    assert real_case.is_stub_test is False
    assert real_case.input_files == []


def test_stub_test_case_always_present() -> None:
    """Every TestSpec always contains exactly one TestCase with is_stub_test=True."""
    test_cases = [
        (["fastq", "paired_end"], "standard_format"),
        (["sorted_bam"], "standard_format"),
        (["vcf_gz"], "generatable"),
        (["count_matrix"], "standard_format"),
    ]
    for format_tags, strategy in test_cases:
        ch = _channel("ch", format_tags, strategy=strategy)
        spec = _minimal_spec([ch])
        console, _ = _capture_console()

        ts = generate_test_spec(spec, _minimal_source(), _standards(), console=console)

        stub_cases = [tc for tc in ts.test_cases if tc.is_stub_test]
        assert len(stub_cases) == 1, (
            f"Expected exactly one stub TestCase for format_tags={format_tags}, "
            f"got {len(stub_cases)}"
        )


def test_summary_table_printed() -> None:
    """Orchestrator prints a Rich table with a 'Strategy' column."""
    ch = _channel("reads", ["fastq", "paired_end"])
    spec = _minimal_spec([ch])
    console, buf = _capture_console()

    generate_test_spec(spec, _minimal_source(), _standards(), console=console)

    assert "Strategy" in buf.getvalue()
