"""Regression tests comparing generated modules against nf-core reference modules.

Minimum score thresholds (history):
  2025-01: initial thresholds — samtools_sort 0.70, fastqc 0.70, trimgalore 0.65
    Removed after first run revealed tool selection issues: samtools is a C binary
    (our discovery finds misc Python scripts, not the sort subcommand); FastQC is
    Java (discovery finds devcontainer shell scripts); TrimGalore is Perl (wrong dir).
  2026-03: revised targets — switched to Python tools our pipeline handles correctly:
    multiqc (Click flat, Python) and cutadapt (argparse, Python).  trimgalore kept
    but with structural-only must_match and low threshold since it's Perl/Bash.
    container_match removed from must_match globally — exact version tags always
    differ between LLM-generated and reference modules.

Run with:
    ANTHROPIC_API_KEY=... pytest -m regression -v

Skip in CI (requires API key + network):
    pytest -m "not regression"
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from code_to_module import convert
from code_to_module.models import ModuleSpec
from code_to_module.regression import ParsedModule, parse_module, semantic_score

# ── Fixture paths ──────────────────────────────────────────────────────────────

_FIXTURES = Path(__file__).parent / "fixtures"
_CHAIN_INPUT = _FIXTURES / "modules" / "chain_input"
_SPECS_DIR = _FIXTURES / "specs"

# ── Reference module targets ──────────────────────────────────────────────────

REFERENCE_MODULES: list[dict] = [
    {
        # Python, Click flat — discovery reliably finds one "multiqc" functionality.
        # nf-core reference: modules/nf-core/multiqc/main.nf (process MULTIQC).
        # container_match excluded: LLM-generated tag will differ from reference tag.
        "id": "multiqc",
        "tool_url": "https://github.com/MultiQC/MultiQC",
        "docs_url": None,
        "reference_path": "modules/nf-core/multiqc",
        "expected_process": "MULTIQC",
        "min_overall_score": 0.45,
        "must_match": ["process_name_match", "has_versions_emit", "has_ext_args"],
    },
    {
        # Python, argparse — discovery reliably finds the cutadapt CLI.
        # nf-core reference: modules/nf-core/cutadapt/main.nf (process CUTADAPT).
        "id": "cutadapt",
        "tool_url": "https://github.com/marcelm/cutadapt",
        "docs_url": None,
        "reference_path": "modules/nf-core/cutadapt",
        "expected_process": "CUTADAPT",
        "min_overall_score": 0.40,
        "must_match": ["process_name_match", "has_versions_emit"],
    },
    {
        # Perl/Bash — kept for non-Python CLI coverage.  process_name_match is
        # best-effort; only structural nf-core invariants are mandatory.
        "id": "trimgalore",
        "tool_url": "https://github.com/FelixKrueger/TrimGalore",
        "docs_url": None,
        "reference_path": "modules/nf-core/trimgalore",
        "expected_process": "TRIMGALORE",
        "min_overall_score": 0.25,
        "must_match": ["has_versions_emit", "has_ext_args"],
    },
]

_SPARSE_PATHS = [t["reference_path"] for t in REFERENCE_MODULES]


# ── Session-scoped fixtures ────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def nfcore_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Sparse-clone nf-core/modules once per session, keeping only target dirs.

    Uses --filter=blob:none + sparse-checkout so only the three module trees
    are downloaded, keeping the clone fast (~seconds instead of minutes).
    Skips the entire fixture if network is unavailable.
    """
    dest = tmp_path_factory.mktemp("nfcore_modules")

    init = subprocess.run(
        [
            "git", "clone",
            "--depth=1",
            "--filter=blob:none",
            "--sparse",
            "https://github.com/nf-core/modules",
            str(dest),
        ],
        capture_output=True,
    )
    if init.returncode != 0:
        pytest.skip(
            "Could not clone nf-core/modules — network unavailable?\n"
            + init.stderr.decode()
        )

    add = subprocess.run(
        ["git", "-C", str(dest), "sparse-checkout", "add", *_SPARSE_PATHS],
        capture_output=True,
    )
    if add.returncode != 0:
        pytest.skip(
            "git sparse-checkout failed:\n" + add.stderr.decode()
        )

    return dest


# ── Helpers ────────────────────────────────────────────────────────────────────


def _find_generated_module(outdir: Path, expected_process: str) -> Path:
    """Find the generated module directory in *outdir* that best matches *expected_process*.

    Scans all immediate subdirectories for a main.nf that contains
    ``process {expected_process}``.  Falls back to the first module found
    when no exact match exists (e.g. for Perl/Bash tools where the process
    name is LLM-derived and may not match the expected value exactly).
    """
    candidates: list[Path] = []
    for d in sorted(outdir.iterdir()):
        if d.is_dir() and (d / "main.nf").exists():
            candidates.append(d)

    if not candidates:
        available = [d.name for d in outdir.iterdir() if d.is_dir()]
        raise ValueError(
            f"No generated module found in {outdir}. "
            f"Available subdirs: {available}"
        )

    for c in candidates:
        if f"process {expected_process}" in (c / "main.nf").read_text():
            return c

    return candidates[0]  # best-effort: return first module found


# ── Regression score tests (llm + network + regression) ───────────────────────


@pytest.mark.regression
@pytest.mark.llm
@pytest.mark.network
@pytest.mark.parametrize("target", REFERENCE_MODULES, ids=[t["id"] for t in REFERENCE_MODULES])
def test_regression_score(
    target: dict, nfcore_cache: Path, tmp_path: Path
) -> None:
    """Generated module meets minimum semantic score against nf-core reference."""
    docs = [target["docs_url"]] if target["docs_url"] else []
    convert(
        source=target["tool_url"],
        outdir=str(tmp_path),
        docs=docs,
        no_lint=True,
    )

    try:
        generated_path = _find_generated_module(tmp_path, target["expected_process"])
    except ValueError as exc:
        pytest.skip(
            f"{target['id']}: no module generated (Tier 5 / unsupported language) — {exc}"
        )
    generated = parse_module(generated_path)
    reference = parse_module(nfcore_cache / target["reference_path"])
    score = semantic_score(generated, reference)

    assert score.overall >= target["min_overall_score"], (
        f"{target['id']}: overall score {score.overall:.2f} below "
        f"minimum {target['min_overall_score']}\n"
        f"Scores: {score}"
    )
    for field in target["must_match"]:
        assert getattr(score, field), (
            f"{target['id']}: must-match field '{field}' failed\n"
            f"Scores: {score}"
        )


# ── Unit tests (no LLM, no network) ───────────────────────────────────────────


def _load_spec() -> ModuleSpec:
    return ModuleSpec.model_validate_json(
        (_SPECS_DIR / "chaintool_spec.json").read_text()
    )


def _generate_chain_module(tmp_path: Path) -> Path:
    """Run convert with injected spec and return the module directory."""
    spec = _load_spec()
    result = convert(
        source=str(_CHAIN_INPUT),
        outdir=str(tmp_path),
        tier_override=1,
        _inject_spec=spec,
        no_lint=True,
    )
    assert result["success"] is True, f"convert failed: {result.get('error')}"
    return tmp_path / "chaintool"


def test_parse_module_on_fixture(tmp_path: Path) -> None:
    """parse_module works on the chain_input fixture from Prompt 23."""
    module_path = _generate_chain_module(tmp_path)
    parsed = parse_module(module_path)

    # process_name comes from chaintool_spec.json CHAINTOOL_RUN
    assert parsed.process_name == "CHAINTOOL_RUN"
    assert parsed.has_versions_emit
    assert not parsed.has_todo  # script_template is set → no TODO comment rendered


def test_parse_module_input_output_names(tmp_path: Path) -> None:
    """parse_module extracts the correct channel names from the generated module."""
    module_path = _generate_chain_module(tmp_path)
    parsed = parse_module(module_path)

    # chaintool spec: one input (reads, type=map) → meta excluded, reads extracted
    assert "reads" in parsed.input_names
    # one output (report, type=file, emit: report) — versions excluded
    assert "report" in parsed.output_names
    assert "versions" not in parsed.output_names


def test_parse_module_container_url(tmp_path: Path) -> None:
    """parse_module extracts the docker container URL."""
    module_path = _generate_chain_module(tmp_path)
    parsed = parse_module(module_path)

    assert parsed.container_docker == "quay.io/biocontainers/chaintool:1.0.0--py_0"


def test_semantic_score_identical_modules(tmp_path: Path) -> None:
    """semantic_score returns overall=1.0 when comparing a module to itself."""
    module_path = _generate_chain_module(tmp_path)
    parsed = parse_module(module_path)
    score = semantic_score(parsed, parsed)

    assert score.overall == pytest.approx(1.0)
    assert score.process_name_match
    assert score.container_match
    assert score.output_coverage == pytest.approx(1.0)
    assert score.input_coverage == pytest.approx(1.0)


def test_semantic_score_empty_reference() -> None:
    """Coverage fields default to 1.0 when the reference has no channels."""
    generated = ParsedModule(
        process_name="TOOL_RUN",
        container_docker="quay.io/biocontainers/tool:1.0",
        input_names=["reads"],
        output_names=["bam"],
        has_versions_emit=True,
        has_ext_args=True,
        has_todo=False,
        script_tool_name="tool",
        keywords=[],
    )
    empty_ref = ParsedModule(
        process_name="TOOL_RUN",
        container_docker="quay.io/biocontainers/tool:1.0",
        input_names=[],
        output_names=[],
        has_versions_emit=True,
        has_ext_args=True,
        has_todo=False,
        script_tool_name="tool",
        keywords=[],
    )
    score = semantic_score(generated, empty_ref)

    assert score.input_coverage == pytest.approx(1.0)
    assert score.output_coverage == pytest.approx(1.0)
    assert score.overall == pytest.approx(1.0)


def test_parse_module_raises_on_missing_main_nf(tmp_path: Path) -> None:
    """parse_module raises ValueError if main.nf is absent."""
    (tmp_path / "meta.yml").write_text("name: test\n")
    with pytest.raises(ValueError, match="main.nf not found"):
        parse_module(tmp_path)


def test_parse_module_raises_on_missing_meta_yml(tmp_path: Path) -> None:
    """parse_module raises ValueError if meta.yml is absent."""
    (tmp_path / "main.nf").write_text("process TOOL {}\n")
    with pytest.raises(ValueError, match="meta.yml not found"):
        parse_module(tmp_path)
