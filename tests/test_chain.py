"""Chain integration tests.

These tests exercise the full convert → review pipeline using a pre-baked
ModuleSpec injected via the _inject_spec seam so that no real LLM or
container-registry calls are made.

Fixture layout:
  tests/fixtures/modules/chain_input/   — synthetic Python CLI tool (chaintool)
  tests/fixtures/specs/chaintool_spec.json — pre-baked ModuleSpec JSON
"""

from __future__ import annotations

from pathlib import Path

from code_to_module import convert, get_functionalities
from code_to_module.models import ModuleSpec
from code_to_module.review import review_module
from code_to_module.standards import get_standards

# ── Fixture paths ──────────────────────────────────────────────────────────────

_FIXTURES = Path(__file__).parent / "fixtures"
_CHAIN_INPUT = _FIXTURES / "modules" / "chain_input"
_SPECS_DIR = _FIXTURES / "specs"


def _load_spec() -> ModuleSpec:
    """Load the pre-baked chaintool ModuleSpec from JSON."""
    return ModuleSpec.model_validate_json(
        (_SPECS_DIR / "chaintool_spec.json").read_text()
    )


# ── Test 1: discovery finds 'run' subcommand ───────────────────────────────────


def test_chain_discovery() -> None:
    """get_functionalities on chain_input detects at least one functionality
    named 'run' (via the argparse add_subparsers / add_parser cascade)."""
    result = get_functionalities(str(_CHAIN_INPUT))

    assert len(result) >= 1, f"No functionalities detected: {result}"
    names = [f["name"] for f in result]
    assert "run" in names, f"Expected 'run' in {names}"


# ── Test 2: convert with injected spec produces module files ──────────────────


def test_chain_convert_produces_module(tmp_path: Path) -> None:
    """convert() with _inject_spec bypasses LLM/container resolution and writes
    the module files to outdir/chaintool/."""
    spec = _load_spec()

    result = convert(
        source=str(_CHAIN_INPUT),
        outdir=str(tmp_path),
        tier_override=1,
        _inject_spec=spec,
        no_lint=True,
    )

    assert result["success"] is True, f"convert failed: {result.get('error')}"
    assert len(result["modules"]) >= 1

    module_dir = tmp_path / "chaintool"
    assert (module_dir / "main.nf").exists(), "main.nf not generated"
    assert (module_dir / "meta.yml").exists(), "meta.yml not generated"
    assert (module_dir / "environment.yml").exists(), "environment.yml not generated"


# ── Test 3: review of generated module has zero errors ────────────────────────


def test_chain_review_after_convert(tmp_path: Path) -> None:
    """The module produced by convert() passes review with error_count == 0.

    This validates that the generated module files satisfy all static-analysis
    rules enforced by review_module() — channel naming, ext.args, meta.yml
    completeness, and conda pinning — without requiring any subprocess calls.
    """
    spec = _load_spec()

    result = convert(
        source=str(_CHAIN_INPUT),
        outdir=str(tmp_path),
        tier_override=1,
        _inject_spec=spec,
        no_lint=True,
    )

    assert result["success"] is True, f"convert failed: {result.get('error')}"

    module_dir = tmp_path / "chaintool"
    report = review_module(module_dir, get_standards())

    assert report.error_count == 0, (
        f"Expected 0 errors, got {report.error_count}:\n"
        + "\n".join(
            f"  [{i.severity}] {i.category}: {i.message}"
            for i in report.items
            if i.severity == "ERROR"
        )
    )


# ── Test 4: generated main.nf satisfies nf-core structural invariants ─────────


def test_chain_main_nf_invariants(tmp_path: Path) -> None:
    """The generated main.nf contains all required nf-core structural elements:
    - correct PROCESS_NAME
    - task.ext.args pattern
    - topic channel for versions
    - emit: versions
    """
    spec = _load_spec()

    result = convert(
        source=str(_CHAIN_INPUT),
        outdir=str(tmp_path),
        tier_override=1,
        _inject_spec=spec,
        no_lint=True,
    )

    assert result["success"] is True, f"convert failed: {result.get('error')}"

    main_nf = (tmp_path / "chaintool" / "main.nf").read_text()

    assert "CHAINTOOL_RUN" in main_nf, "Process name missing"
    assert "task.ext.args" in main_nf, "task.ext.args pattern missing"
    assert "topic: versions" in main_nf, "versions topic channel missing"
    assert "emit: versions" in main_nf, "versions emit missing"
    assert "quay.io/biocontainers/chaintool" in main_nf, "Docker container URL missing"
