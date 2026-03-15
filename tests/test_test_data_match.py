"""Tests for test_data_match.py."""

from __future__ import annotations

from code_to_module.models import ChannelSpec
from code_to_module.standards import Standards
from code_to_module.test_data_match import match

# ── Minimal schema skeleton for mock Standards ─────────────────────────────────

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
    "known_tools": ["samtools", "bwa"],
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
    "derivation_templates": {},
    "chain_modules": {},
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
            "id": "sarscov2_fastq_single",
            "paths": ["genomics/sarscov2/illumina/fastq/test.fastq.gz"],
            "tags": ["fastq", "single_end", "illumina"],
            "organism": "sarscov2",
            "size_kb": 45,
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
        {
            "id": "sarscov2_fasta",
            "paths": [
                "genomics/sarscov2/genome/genome.fasta",
                "genomics/sarscov2/genome/genome.fasta.fai",
            ],
            "tags": ["fasta", "fasta_index", "genome"],
            "organism": "sarscov2",
            "size_kb": 35,
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
            "id": "sarscov2_bed",
            "paths": ["genomics/sarscov2/genome/bed8/test.bed"],
            "tags": ["bed"],
            "organism": "sarscov2",
            "size_kb": 2,
        },
        {
            "id": "sarscov2_gtf",
            "paths": ["genomics/sarscov2/genome/genome.gtf"],
            "tags": ["gtf", "annotation"],
            "organism": "sarscov2",
            "size_kb": 8,
        },
    ],
}


def _standards() -> Standards:
    return Standards.from_dict(_BASE_SCHEMA)


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_fastq_paired_match() -> None:
    """format_tags=['fastq','paired_end'] → sarscov2_fastq_paired."""
    channel = ChannelSpec(
        name="reads",
        type="map",
        description="Paired-end FASTQ reads",
        format_tags=["fastq", "paired_end"],
    )
    result = match(channel, _standards())
    assert result is not None
    assert result.id == "sarscov2_fastq_paired"
    assert len(result.paths) == 2
    assert all("fastq" in p for p in result.paths)
    assert result.organism == "sarscov2"
    assert len(result.resolved_paths) == 2
    assert all(
        p.startswith("https://raw.githubusercontent.com/nf-core/test-datasets/modules/")
        for p in result.resolved_paths
    )


def test_fastq_single_match() -> None:
    """format_tags=['fastq','single_end'] → sarscov2_fastq_single."""
    channel = ChannelSpec(
        name="reads",
        type="map",
        description="Single-end FASTQ reads",
        format_tags=["fastq", "single_end"],
    )
    result = match(channel, _standards())
    assert result is not None
    assert result.id == "sarscov2_fastq_single"
    assert len(result.paths) == 1


def test_bam_match() -> None:
    """format_tags=['bam'] → sarscov2_bam."""
    channel = ChannelSpec(
        name="bam",
        type="map",
        description="Sorted BAM file",
        format_tags=["bam"],
    )
    result = match(channel, _standards())
    assert result is not None
    assert result.id == "sarscov2_bam"
    assert any(".bam" in p for p in result.paths)


def test_vcf_match() -> None:
    """format_tags=['vcf_gz'] → sarscov2_vcf."""
    channel = ChannelSpec(
        name="vcf",
        type="map",
        description="Compressed VCF",
        format_tags=["vcf_gz"],
    )
    result = match(channel, _standards())
    assert result is not None
    assert result.id == "sarscov2_vcf"
    assert result.size_kb == 15


def test_no_match_custom() -> None:
    """format_tags=['custom_proprietary_format'] → None."""
    channel = ChannelSpec(
        name="data",
        type="map",
        description="Proprietary data",
        format_tags=["custom_proprietary_format"],
    )
    assert match(channel, _standards()) is None


def test_no_match_unknown_format() -> None:
    """Empty format_tags → None (no tags to match on)."""
    channel = ChannelSpec(
        name="data",
        type="map",
        description="Unknown format",
        format_tags=[],
    )
    assert match(channel, _standards()) is None


def test_prefers_smallest() -> None:
    """When multiple entries share a tag, the smallest (size_kb) is returned."""
    # Both sarscov2_fastq_paired (90 kb) and sarscov2_fastq_single (45 kb) carry 'fastq'.
    # With only ["fastq"] as the query, both match; the single-end file is smaller.
    channel = ChannelSpec(
        name="reads",
        type="map",
        description="Any FASTQ",
        format_tags=["fastq"],
    )
    result = match(channel, _standards())
    assert result is not None
    assert result.id == "sarscov2_fastq_single"  # 45 kb < 90 kb
    assert result.size_kb == 45
