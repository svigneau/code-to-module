"""Contract tests for the standards/ subpackage.

These tests are intentionally written BEFORE the implementation exists (Prompt 5).
All tests in Parts 1–3 will fail with AttributeError or NotImplementedError until
Prompt 6 populates the Standards class and schema.

Part 4 (no-hardcoding AST tests) will fail with AttributeError for the same reason,
and additionally enforce that no nf-core convention is baked into application code
once the implementations (Prompts 7+) land.

Run with:  pytest tests/test_standards.py -v
"""

from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from code_to_module.standards import (  # noqa: F401
    Standards,
    chain_module_for,
    derivation_template_for,
    find_test_data,
    get_standards,
)

# ── Constants ─────────────────────────────────────────────────────────────────

SRC_ROOT = Path(__file__).parent.parent / "src" / "code_to_module"

# Minimum valid schema for staleness/update unit tests — built inline so tests
# remain independent of the real bundled schema.
_TODAY = date.today().isoformat()
_60_DAYS_AGO = (date.today() - timedelta(days=60)).isoformat()

_MINIMAL_SCHEMA: dict = {
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
        "tier1_max": 1,
        "tier2_max": 2,
        "tier3_max": 3,
        "tier4_max": 4,
        "pre_select_min_confidence": 0.70,
    },
    "helper_filename_patterns": ["utils", "helpers", "common", "config", "setup"],
    "helper_dirname_patterns": ["tests", "test", "docs", "examples", ".github"],
    "test_data_base_path": "https://raw.githubusercontent.com/nf-core/test-datasets/",
    "test_data_index": [
        {
            "id": "test_paired_end_fastq",
            "paths": ["testdata/test_1.fastq.gz", "testdata/test_2.fastq.gz"],
            "tags": ["fastq", "paired_end"],
            "organism": "human",
            "size_kb": 100,
        },
        {
            "id": "test_single_end_fastq",
            "paths": ["testdata/test.fastq.gz"],
            "tags": ["fastq", "single_end"],
            "organism": "human",
            "size_kb": 50,
        },
        {
            "id": "test_bam",
            "paths": ["testdata/test.bam"],
            "tags": ["bam", "sorted_bam"],
            "organism": "human",
            "size_kb": 200,
        },
        {
            "id": "test_vcf_gz",
            "paths": ["testdata/test.vcf.gz"],
            "tags": ["vcf_gz", "vcf"],
            "organism": "human",
            "size_kb": 30,
        },
        {
            "id": "test_fasta",
            "paths": ["testdata/test.fa"],
            "tags": ["fasta"],
            "organism": "human",
            "size_kb": 500,
        },
    ],
    "derivation_templates": {
        "vcf_gz": {
            "template": "subset_vcf.sh.j2",
            "tool_requirements": ["bcftools"],
            "applicable_tags": ["vcf_gz", "vcf"],
            "source_id": "test_vcf_gz",
        },
    },
    "chain_modules": {
        "sorted_bam": {
            "module": "modules/nf-core/samtools/sort/main.nf",
            "process_name": "SAMTOOLS_SORT",
            "test_input_path": "testdata/test.bam",
            "output_channel": "bam",
        },
    },
}


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def standards() -> Standards:
    """Load the real bundled Standards once per module."""
    return Standards()


# ── Part 1: Schema accessor contract tests ────────────────────────────────────


def test_bundled_schema_loads(standards: Standards) -> None:
    assert isinstance(standards.valid_labels, list)
    assert len(standards.valid_labels) > 0
    assert all(isinstance(lbl, str) for lbl in standards.valid_labels)


def test_valid_labels_are_known_values(standards: Standards) -> None:
    assert set(standards.valid_labels) == {
        "process_single",
        "process_medium",
        "process_high",
        "process_high_memory",
    }


def test_label_resources_all_have_required_keys(standards: Standards) -> None:
    for label in standards.valid_labels:
        resources = standards.label_resources[label]
        for key in ("cpus", "memory_gb", "time_h"):
            assert key in resources, f"label_resources['{label}'] missing key '{key}'"
            assert isinstance(resources[key], (int, float)), (
                f"label_resources['{label}']['{key}'] must be numeric"
            )


def test_versions_use_topic_channels_is_bool(standards: Standards) -> None:
    assert isinstance(standards.versions_use_topic_channels, bool)
    assert standards.versions_use_topic_channels is True


def test_docker_registry_format(standards: Standards) -> None:
    assert isinstance(standards.docker_registry, str)
    assert standards.docker_registry.startswith("quay.io/")


def test_singularity_registry_format(standards: Standards) -> None:
    assert isinstance(standards.singularity_registry, str)
    assert standards.singularity_registry.startswith("https://depot.galaxyproject.org/")


def test_conda_channels_order(standards: Standards) -> None:
    assert standards.conda_channels == ["conda-forge", "bioconda", "defaults"]


def test_meta_yml_required_fields_non_empty(standards: Standards) -> None:
    fields = standards.meta_yml_required_fields
    assert isinstance(fields, list)
    for required in ("name", "description", "tools", "input", "output"):
        assert required in fields, f"meta_yml_required_fields missing '{required}'"


def test_ext_args_pattern_present(standards: Standards) -> None:
    assert isinstance(standards.ext_args_pattern, str)
    assert standards.ext_args_pattern  # non-empty
    assert "task.ext.args" in standards.ext_args_pattern


def test_ext_prefix_pattern_present(standards: Standards) -> None:
    assert isinstance(standards.ext_prefix_pattern, str)
    assert standards.ext_prefix_pattern  # non-empty
    assert "task.ext.prefix" in standards.ext_prefix_pattern
    assert "meta.id" in standards.ext_prefix_pattern


def test_known_tools_minimum_count(standards: Standards) -> None:
    tools = standards.known_tools
    assert len(tools) >= 10
    for tool in ("samtools", "bwa", "bcftools", "fastqc"):
        assert tool in tools, f"known_tools missing expected tool '{tool}'"


def test_tier_thresholds_has_required_keys(standards: Standards) -> None:
    thresholds = standards.tier_thresholds
    for key in ("tier1_max", "tier2_max", "tier3_max", "tier4_max", "pre_select_min_confidence"):
        assert key in thresholds, f"tier_thresholds missing key '{key}'"


def test_helper_filename_patterns_non_empty(standards: Standards) -> None:
    patterns = standards.helper_filename_patterns
    assert isinstance(patterns, list)
    assert len(patterns) >= 1
    common = {"utils", "helpers", "common", "config"}
    assert common & set(patterns), (
        f"helper_filename_patterns should include at least one of {common}"
    )


def test_helper_dirname_patterns_non_empty(standards: Standards) -> None:
    patterns = standards.helper_dirname_patterns
    assert isinstance(patterns, list)
    assert len(patterns) >= 1


# ── Part 2: Test data index accessor tests ────────────────────────────────────


def test_test_data_base_path_is_url(standards: Standards) -> None:
    assert isinstance(standards.test_data_base_path, str)
    assert standards.test_data_base_path.startswith("https://")


def test_test_data_index_non_empty(standards: Standards) -> None:
    index = standards.test_data_index
    assert isinstance(index, list)
    assert len(index) >= 5
    for entry in index:
        for key in ("id", "paths", "tags", "organism", "size_kb"):
            assert key in entry, f"test_data_index entry missing key '{key}'"


def test_find_test_data_fastq_paired(standards: Standards) -> None:
    results = find_test_data(["fastq", "paired_end"])
    assert len(results) >= 1
    for entry in results:
        assert "fastq" in entry["tags"]
        assert "paired_end" in entry["tags"]


def test_find_test_data_sorted_by_size(standards: Standards) -> None:
    results = find_test_data(["fastq"])
    assert len(results) >= 1
    sizes = [r["size_kb"] for r in results]
    assert sizes == sorted(sizes), "find_test_data results must be sorted by size_kb ascending"


def test_find_test_data_no_match_returns_empty(standards: Standards) -> None:
    results = find_test_data(["count_matrix"])
    assert results == []


def test_derivation_template_for_vcf(standards: Standards) -> None:
    tmpl = derivation_template_for(["vcf_gz"])
    assert tmpl is not None
    assert isinstance(tmpl, dict)
    for key in ("template", "tool_requirements", "applicable_tags", "source_id"):
        assert key in tmpl, f"derivation template missing key '{key}'"
    assert tmpl["template"].endswith(".sh.j2")


def test_derivation_template_for_unknown(standards: Standards) -> None:
    assert derivation_template_for(["count_matrix"]) is None


def test_chain_module_for_sorted_bam(standards: Standards) -> None:
    result = chain_module_for(["sorted_bam"])
    assert result is not None
    assert isinstance(result, dict)
    for key in ("module", "process_name", "test_input_path", "output_channel"):
        assert key in result, f"chain_module result missing key '{key}'"
    assert "samtools/sort" in result["module"]


def test_chain_module_for_unknown(standards: Standards) -> None:
    assert chain_module_for(["count_matrix"]) is None


# ── Part 3: Staleness and update tests ───────────────────────────────────────


def test_is_stale_false_for_recent_date() -> None:
    s = Standards.from_dict({**_MINIMAL_SCHEMA, "last_updated": _TODAY})
    assert s.is_stale(max_age_days=30) is False


def test_is_stale_true_for_old_date() -> None:
    s = Standards.from_dict({**_MINIMAL_SCHEMA, "last_updated": _60_DAYS_AGO})
    assert s.is_stale(max_age_days=30) is True


def test_check_for_updates_returns_none_when_same_version() -> None:
    s = Standards.from_dict(_MINIMAL_SCHEMA)
    with patch("code_to_module.standards.loader.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"schema_version": _MINIMAL_SCHEMA["schema_version"]}
        result = s.check_for_updates()
    assert result is None


def test_check_for_updates_returns_version_string_when_newer() -> None:
    s = Standards.from_dict(_MINIMAL_SCHEMA)
    with patch("code_to_module.standards.loader.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"schema_version": "3.6.0"}
        result = s.check_for_updates()
    assert result == "3.6.0"


def test_check_for_updates_returns_none_on_timeout() -> None:
    import httpx

    s = Standards.from_dict(_MINIMAL_SCHEMA)
    with patch(
        "code_to_module.standards.loader.httpx.get",
        side_effect=httpx.TimeoutException("timed out"),
    ):
        result = s.check_for_updates()  # must not raise
    assert result is None


def test_fetch_and_save_writes_file(tmp_path: Path) -> None:
    schema_path = tmp_path / "nf_core_standards.json"
    with patch("code_to_module.standards.loader.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = _MINIMAL_SCHEMA
        loaded = Standards.fetch_and_save(schema_path)
    assert schema_path.is_file()
    assert isinstance(loaded, Standards)
    assert loaded.valid_labels  # loads without error


def test_fetch_and_save_raises_on_malformed(tmp_path: Path) -> None:
    schema_path = tmp_path / "nf_core_standards.json"
    original_content = '{"schema_version": "3.5.0"}'
    schema_path.write_text(original_content, encoding="utf-8")

    with patch("code_to_module.standards.loader.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"not": "a schema"}
        with pytest.raises(ValueError):
            Standards.fetch_and_save(schema_path)

    # Original file unchanged
    assert schema_path.read_text(encoding="utf-8") == original_content


def test_get_standards_singleton() -> None:
    s1 = get_standards()
    s2 = get_standards()
    assert s1 is s2


# ── Part 4: No-hardcoding enforcement (AST) ───────────────────────────────────


def _scan_for_exact_literals(
    filepath: Path,
    forbidden: set[str],
) -> list[tuple[int, str]]:
    """Return [(line_no, value)] for string constants in forbidden."""
    if not filepath.is_file():
        return []
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in forbidden
    ]


def _scan_for_url_prefixes(
    filepath: Path,
    prefixes: tuple[str, ...],
) -> list[tuple[int, str]]:
    """Return [(line_no, value)] for string constants starting with a forbidden prefix."""
    if not filepath.is_file():
        return []
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and any(node.value.startswith(p) for p in prefixes)
    ]


def _scan_for_channel_list(filepath: Path) -> list[int]:
    """Return line numbers where the conda channel list is hardcoded inline."""
    if not filepath.is_file():
        return []
    channels = ["conda-forge", "bioconda", "defaults"]
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.List):
            continue
        values = [
            e.value
            for e in node.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
        if values == channels:
            hits.append(node.lineno)
    return hits


_LABEL_FILES = ["assess.py", "infer.py", "generate.py", "quick_lint.py", "container.py"]
_REGISTRY_FILES = ["container.py", "infer.py", "generate.py"]
_KNOWN_TOOLS_FILES = ["assess.py", "discover.py"]
_CHANNEL_FILES = ["generate.py", "container.py"]


@pytest.mark.parametrize("filename", _LABEL_FILES)
def test_no_hardcoded_process_labels(filename: str) -> None:
    """No application file may hard-code a process label string."""
    s = Standards()
    forbidden = set(s.valid_labels)
    matches = _scan_for_exact_literals(SRC_ROOT / filename, forbidden)
    for lineno, literal in matches:
        pytest.fail(
            f"Hardcoded label '{literal}' found in {filename} at line {lineno}. "
            "Use standards.valid_labels instead."
        )


@pytest.mark.parametrize("filename", _REGISTRY_FILES)
def test_no_hardcoded_registry_urls(filename: str) -> None:
    """No application file may hard-code a container registry URL prefix."""
    prefixes = (
        "quay.io/biocontainers/",
        "https://depot.galaxyproject.org/singularity/",
    )
    matches = _scan_for_url_prefixes(SRC_ROOT / filename, prefixes)
    for lineno, literal in matches:
        pytest.fail(
            f"Hardcoded registry URL '{literal[:60]}...' found in {filename} "
            f"at line {lineno}. Use standards.docker_registry / "
            "standards.singularity_registry instead."
        )


@pytest.mark.parametrize("filename", _KNOWN_TOOLS_FILES)
def test_no_hardcoded_known_tools(filename: str) -> None:
    """No application file may hard-code a known tool name."""
    s = Standards()
    forbidden = set(s.known_tools)
    matches = _scan_for_exact_literals(SRC_ROOT / filename, forbidden)
    for lineno, literal in matches:
        pytest.fail(
            f"Hardcoded tool name '{literal}' found in {filename} at line {lineno}. "
            "Use standards.known_tools instead."
        )


@pytest.mark.parametrize("filename", _CHANNEL_FILES)
def test_no_hardcoded_conda_channels(filename: str) -> None:
    """No application file may hard-code the conda channel list inline."""
    hits = _scan_for_channel_list(SRC_ROOT / filename)
    for lineno in hits:
        pytest.fail(
            f"Hardcoded conda channel list found in {filename} at line {lineno}. "
            "Use standards.conda_channels instead."
        )
