"""Tests for the convert, assess-only, and containers CLI commands.

cli.py is a thin display layer — it calls api.convert() for the full pipeline.
Tests patch code_to_module.ingest.ingest (cli calls it directly for the scan
summary) and code_to_module.api.convert (the actual pipeline delegation).
The dry-run path bypasses the API and uses discover directly, so those patches
target code_to_module.discover.*.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from code_to_module.cli import main
from code_to_module.models import (
    CodeSource,
    ContainerHint,
    DetectionMethod,
    DiscoveryResult,
    FunctionalitySpec,
)

# ── Shared fixtures ────────────────────────────────────────────────────────────


def _make_func(
    name: str = "run",
    confidence: float = 0.9,
    detection: DetectionMethod = DetectionMethod.SINGLE_SCRIPT,
    pre_selected: bool = True,
) -> FunctionalitySpec:
    return FunctionalitySpec(
        name=name,
        display_name=name.capitalize(),
        description=f"Run {name}",
        detection_method=detection,
        confidence=confidence,
        code_section="samtools sort -o out.bam in.bam",
        pre_selected=pre_selected,
    )


def _make_source(tmp_path: Path, filename: str = "script.py", **hint_kwargs) -> CodeSource:
    script = tmp_path / filename
    script.write_text("print('hello')")
    hint = ContainerHint(**hint_kwargs) if hint_kwargs else None
    return CodeSource(
        source_type="file",
        path=script,
        language="python",
        raw_code="print('hello')",
        filename=filename,
        container_hint=hint,
    )


def _make_discovery(funcs: list[FunctionalitySpec]) -> DiscoveryResult:
    return DiscoveryResult(
        source=CodeSource(
            source_type="file",
            path=Path("/fake/script.py"),
            language="python",
            raw_code="",
            filename="script.py",
        ),
        functionalities=funcs,
        selected=[f for f in funcs if f.pre_selected],
        detection_method_used=DetectionMethod.SINGLE_SCRIPT,
        is_single_functionality=len(funcs) == 1,
    )


def _api_result(
    tmp_path: Path,
    func_names: list[str] | None = None,
    tier: int = 1,
) -> dict:
    """Build a minimal successful api.convert() return value."""
    names = func_names or ["run"]
    return {
        "success": True,
        "modules": [
            {
                "functionality_name": name,
                "process_name": f"{name.upper()}_RUN",
                "tier": tier,
                "confidence": 0.9,
                "container_source": "biocontainers",
                "container_docker": "quay.io/biocontainers/mytool:1.0.0",
                "test_data_strategies": {},
                "needs_derivation": False,
                "files_created": [str(tmp_path / "main.nf")],
                "warnings": [],
                "module_spec": {"tool_name": name},
            }
            for name in names
        ],
        "functionalities_found": names,
        "functionalities_selected": names,
        "detection_method": "single_script",
        "error": None,
    }


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_single_functionality(tmp_path: Path) -> None:
    """Single script → one module generated, exit 0."""
    runner = CliRunner()
    source = _make_source(tmp_path)
    api_result = _api_result(tmp_path)

    with (
        patch("code_to_module.ingest.ingest", return_value=source),
        patch("code_to_module.api.convert", return_value=api_result) as mock_api,
    ):
        result = runner.invoke(main, [
            "convert", str(tmp_path / "script.py"),
            "--outdir", str(tmp_path / "out"),
            "--no-interaction",
        ])

    assert result.exit_code == 0, result.output
    mock_api.assert_called_once()


def test_multi_functionality_interactive(tmp_path: Path) -> None:
    """Multi-functionality: two modules in result → both displayed, exit 0."""
    runner = CliRunner()
    source = _make_source(tmp_path)
    api_result = _api_result(tmp_path, func_names=["align", "sort"])

    with (
        patch("code_to_module.ingest.ingest", return_value=source),
        patch("code_to_module.api.convert", return_value=api_result),
    ):
        result = runner.invoke(main, [
            "convert", str(tmp_path),
            "--outdir", str(tmp_path / "out"),
            "--no-interaction",
        ])

    assert result.exit_code == 0, result.output
    assert "align" in result.output
    assert "sort" in result.output


def test_all_functionalities_flag(tmp_path: Path) -> None:
    """--all-functionalities → api.convert called with functionalities=None."""
    runner = CliRunner()
    source = _make_source(tmp_path)
    api_result = _api_result(tmp_path, func_names=["align", "sort", "index"])

    with (
        patch("code_to_module.ingest.ingest", return_value=source),
        patch("code_to_module.api.convert", return_value=api_result) as mock_api,
    ):
        result = runner.invoke(main, [
            "convert", str(tmp_path),
            "--outdir", str(tmp_path / "out"),
            "--all-functionalities", "--no-interaction",
        ])

    assert result.exit_code == 0, result.output
    # --all-functionalities means no name filter is passed to the API
    call_kwargs = mock_api.call_args
    assert call_kwargs.kwargs.get("functionalities") is None


def test_functionalities_flag(tmp_path: Path) -> None:
    """--functionalities align → api.convert called with functionalities='align'."""
    runner = CliRunner()
    source = _make_source(tmp_path)
    api_result = _api_result(tmp_path, func_names=["align"])

    with (
        patch("code_to_module.ingest.ingest", return_value=source),
        patch("code_to_module.api.convert", return_value=api_result) as mock_api,
    ):
        result = runner.invoke(main, [
            "convert", str(tmp_path),
            "--outdir", str(tmp_path / "out"),
            "--functionalities", "align", "--no-interaction",
        ])

    assert result.exit_code == 0, result.output
    call_kwargs = mock_api.call_args
    assert call_kwargs.kwargs.get("functionalities") == "align"


def test_dry_run_multi(tmp_path: Path) -> None:
    """--dry-run: discover path, prints 'Would generate 3', exit 0, API not called."""
    runner = CliRunner()
    funcs = [_make_func("align"), _make_func("sort"), _make_func("index")]
    discovery = _make_discovery(funcs)
    discovery = discovery.model_copy(update={"selected": funcs, "is_single_functionality": False})
    source = _make_source(tmp_path)

    with (
        patch("code_to_module.ingest.ingest", return_value=source),
        patch("code_to_module.discover.discover", return_value=discovery),
        patch("code_to_module.discover.select_functionalities", return_value=discovery),
        patch("code_to_module.api.convert") as mock_api,
    ):
        result = runner.invoke(main, [
            "convert", str(tmp_path),
            "--outdir", str(tmp_path / "out"),
            "--dry-run", "--no-interaction",
        ])

    assert result.exit_code == 0, result.output
    assert "Would generate 3" in result.output
    mock_api.assert_not_called()


def test_tier5_skipped(tmp_path: Path) -> None:
    """Tier 5 modules in result are shown as skipped; generated modules exit 0."""
    runner = CliRunner()
    source = _make_source(tmp_path)

    # One good module (has files) and one skipped (no files, tier 5)
    api_result = {
        "success": True,
        "modules": [
            {
                "functionality_name": "good",
                "process_name": "GOOD_RUN",
                "tier": 1,
                "confidence": 0.9,
                "container_source": "biocontainers",
                "container_docker": "quay.io/biocontainers/mytool:1.0.0",
                "test_data_strategies": {},
                "needs_derivation": False,
                "files_created": [str(tmp_path / "main.nf")],
                "warnings": [],
                "module_spec": {"tool_name": "good"},
            },
            {
                "functionality_name": "broken",
                "process_name": "",
                "tier": 5,
                "confidence": 0.3,
                "container_source": "",
                "container_docker": "",
                "test_data_strategies": {},
                "needs_derivation": False,
                "files_created": [],
                "warnings": ["Tier 5: requires manual module creation."],
                "module_spec": {},
            },
        ],
        "functionalities_found": ["good", "broken"],
        "functionalities_selected": ["good", "broken"],
        "detection_method": "single_script",
        "error": None,
    }

    with (
        patch("code_to_module.ingest.ingest", return_value=source),
        patch("code_to_module.api.convert", return_value=api_result),
    ):
        result = runner.invoke(main, [
            "convert", str(tmp_path),
            "--outdir", str(tmp_path / "out"), "--no-interaction",
        ])

    assert result.exit_code == 0, result.output
    assert "Skipping broken" in result.output or "Tier 5" in result.output


def test_container_per_functionality(tmp_path: Path) -> None:
    """api.convert is called with the correct container flag."""
    runner = CliRunner()
    source = _make_source(tmp_path)
    api_result = _api_result(tmp_path, func_names=["align", "sort"])

    with (
        patch("code_to_module.ingest.ingest", return_value=source),
        patch("code_to_module.api.convert", return_value=api_result) as mock_api,
    ):
        result = runner.invoke(main, [
            "convert", str(tmp_path),
            "--outdir", str(tmp_path / "out"),
            "--container", "biocontainers", "--no-interaction",
        ])

    assert result.exit_code == 0, result.output
    call_kwargs = mock_api.call_args
    assert call_kwargs.kwargs.get("container") == "biocontainers"


def test_no_functionalities_selected_exits(tmp_path: Path) -> None:
    """api.convert returns empty selected list → exit code 2."""
    runner = CliRunner()
    source = _make_source(tmp_path)

    api_result = {
        "success": True,
        "modules": [],
        "functionalities_found": ["align"],
        "functionalities_selected": [],
        "detection_method": "single_script",
        "error": None,
    }

    with (
        patch("code_to_module.ingest.ingest", return_value=source),
        patch("code_to_module.api.convert", return_value=api_result),
    ):
        result = runner.invoke(main, [
            "convert", str(tmp_path),
            "--outdir", str(tmp_path / "out"),
            "--functionalities", "nonexistent", "--no-interaction",
        ])

    assert result.exit_code == 2, result.output
