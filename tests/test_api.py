"""Tests for api.py — the clean public API.

All calls to ingest, assess, infer, container, and generate are mocked.
No real API calls, no file I/O beyond what the API itself does.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

from code_to_module.models import (
    ChannelSpec,
    CodeSource,
    ContainerDiscovery,
    ContainerOption,
    ContainerSource,
    DetectionMethod,
    DiscoveryResult,
    FunctionalitySpec,
    ModuleSpec,
    TestCase,
    TestSpec,
)

# ── Shared fixtures ────────────────────────────────────────────────────────────


def _make_source(tmp_path: Path, filename: str = "script.py") -> CodeSource:
    script = tmp_path / filename
    script.write_text("print('hello')")
    return CodeSource(
        source_type="file",
        path=script,
        language="python",
        raw_code="print('hello')",
        filename=filename,
    )


def _make_func(name: str = "run", confidence: float = 0.9) -> FunctionalitySpec:
    return FunctionalitySpec(
        name=name,
        display_name=name.capitalize(),
        description=f"Run {name}",
        detection_method=DetectionMethod.SINGLE_SCRIPT,
        confidence=confidence,
        code_section="",
        pre_selected=True,
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
        selected=funcs,
        detection_method_used=DetectionMethod.SINGLE_SCRIPT,
        is_single_functionality=len(funcs) == 1,
    )


def _make_spec(tool_name: str = "mytool", process_name: str = "MYTOOL_RUN") -> ModuleSpec:
    return ModuleSpec(
        tool_name=tool_name,
        process_name=process_name,
        functionality_name="run",
        inputs=[ChannelSpec(name="input", type="file", description="Input file")],
        outputs=[ChannelSpec(name="out", type="file", description="Output file")],
        container_docker="quay.io/biocontainers/mytool:1.0.0--pyhdfd78af_0",
        container_singularity="https://depot.galaxyproject.org/singularity/mytool:1.0.0",
        container_source=ContainerSource.BIOCONTAINERS,
        label="process_single",
        ext_args="def args = task.ext.args ?: ''",
        tier=1,
        confidence=0.9,
    )


def _make_test_spec() -> TestSpec:
    return TestSpec(
        process_name="MYTOOL_RUN",
        test_cases=[TestCase(name="test_mytool", is_stub_test=False)],
        needs_derivation=False,
    )


def _make_container_opt(source: ContainerSource = ContainerSource.BIOCONTAINERS) -> ContainerOption:
    return ContainerOption(
        source=source,
        label=source.value,
        docker_url="quay.io/biocontainers/mytool:1.0.0--pyhdfd78af_0",
        singularity_url="https://depot.galaxyproject.org/singularity/mytool:1.0.0",
        is_default=True,
    )


def _full_patches(tmp_path: Path, funcs: list[FunctionalitySpec] | None = None):
    """Return a context manager dict that patches the full pipeline."""
    if funcs is None:
        funcs = [_make_func()]
    source = _make_source(tmp_path)
    discovery = _make_discovery(funcs)
    spec = _make_spec()
    test_spec = _make_test_spec()
    container_opt = _make_container_opt()
    created_paths = [tmp_path / "main.nf"]

    return {
        "ingest": patch("code_to_module.api.ingest", return_value=source),
        "discover": patch("code_to_module.api.discover", return_value=discovery),
        "select": patch("code_to_module.api.select_functionalities", return_value=discovery),
        "assess": patch("code_to_module.api.assess", return_value=(1, 0.9, [])),
        "resolve": patch("code_to_module.api.resolve", return_value=container_opt),
        "infer": patch("code_to_module.api.infer_module_spec_sync", return_value=spec),
        "test_gen": patch("code_to_module.api.generate_test_spec", return_value=test_spec),
        "generate": patch("code_to_module.api.generate", return_value=created_paths),
        "lint": patch("code_to_module.api.quick_lint", return_value=[]),
    }


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_convert_returns_structured_dict(tmp_path: Path) -> None:
    """Mocked internals → result has all required top-level keys."""
    from code_to_module.api import convert

    patches = _full_patches(tmp_path)
    with (
        patches["ingest"],
        patches["discover"],
        patches["select"],
        patches["assess"],
        patches["resolve"],
        patches["infer"],
        patches["test_gen"],
        patches["generate"],
        patches["lint"],
    ):
        result = convert(source="script.py", outdir=str(tmp_path))

    assert result["success"] is True
    assert result["error"] is None
    assert isinstance(result["modules"], list)
    assert isinstance(result["functionalities_found"], list)
    assert isinstance(result["functionalities_selected"], list)
    assert isinstance(result["detection_method"], str)

    # Each module entry has required keys
    mod = result["modules"][0]
    for key in (
        "functionality_name", "process_name", "tier", "confidence",
        "container_source", "container_docker", "test_data_strategies",
        "needs_derivation", "files_created", "warnings", "module_spec",
    ):
        assert key in mod, f"module entry missing key: {key}"


def test_convert_modules_count(tmp_path: Path) -> None:
    """Two functionalities selected → 'modules' list has two entries."""
    from code_to_module.api import convert

    funcs = [_make_func("align"), _make_func("sort")]
    discovery = _make_discovery(funcs)
    source = _make_source(tmp_path)
    spec1 = _make_spec("align", "ALIGN_RUN")
    spec2 = _make_spec("sort", "SORT_RUN")
    test_spec = _make_test_spec()
    container_opt = _make_container_opt()
    created_paths = [tmp_path / "main.nf"]

    with (
        patch("code_to_module.api.ingest", return_value=source),
        patch("code_to_module.api.discover", return_value=discovery),
        patch("code_to_module.api.select_functionalities", return_value=discovery),
        patch("code_to_module.api.assess", return_value=(1, 0.9, [])),
        patch("code_to_module.api.resolve", return_value=container_opt),
        patch("code_to_module.api.infer_module_spec_sync", side_effect=[spec1, spec2]),
        patch("code_to_module.api.generate_test_spec", return_value=test_spec),
        patch("code_to_module.api.generate", return_value=created_paths),
        patch("code_to_module.api.quick_lint", return_value=[]),
    ):
        result = convert(source="script.py", outdir=str(tmp_path))

    assert result["success"] is True
    assert len(result["modules"]) == 2


def test_convert_no_rich_output(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """api.convert() produces no stdout (no Rich panels or status lines)."""
    from code_to_module.api import convert

    patches = _full_patches(tmp_path)
    with (
        patches["ingest"],
        patches["discover"],
        patches["select"],
        patches["assess"],
        patches["resolve"],
        patches["infer"],
        patches["test_gen"],
        patches["generate"],
        patches["lint"],
    ):
        convert(source="script.py", outdir=str(tmp_path))

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_convert_dry_run(tmp_path: Path) -> None:
    """dry_run=True → files_created is empty list, success=True."""
    from code_to_module.api import convert

    funcs = [_make_func("run")]
    source = _make_source(tmp_path)
    discovery = _make_discovery(funcs)

    with (
        patch("code_to_module.api.ingest", return_value=source),
        patch("code_to_module.api.discover", return_value=discovery),
        patch("code_to_module.api.select_functionalities", return_value=discovery),
        patch("code_to_module.api.generate") as mock_gen,
    ):
        result = convert(source="script.py", outdir=str(tmp_path), dry_run=True)

    assert result["success"] is True
    assert result["error"] is None
    mock_gen.assert_not_called()
    for mod in result["modules"]:
        assert mod["files_created"] == []


def test_convert_error_handling(tmp_path: Path) -> None:
    """ingest raises exception → result['success']=False, result['error'] has message."""
    from code_to_module.api import convert

    with patch("code_to_module.api.ingest", side_effect=RuntimeError("bad source")):
        result = convert(source="/nonexistent", outdir=str(tmp_path))

    assert result["success"] is False
    assert "bad source" in result["error"]
    assert result["modules"] == []


def test_get_functionalities_fields(tmp_path: Path) -> None:
    """get_functionalities() returns list with required keys."""
    from code_to_module.api import get_functionalities

    funcs = [_make_func("align"), _make_func("sort")]
    source = _make_source(tmp_path)
    discovery = _make_discovery(funcs)

    with (
        patch("code_to_module.api.ingest", return_value=source),
        patch("code_to_module.api.discover", return_value=discovery),
    ):
        result = get_functionalities("script.py")

    assert len(result) == 2
    for entry in result:
        for key in ("name", "display_name", "description", "detection_method",
                    "confidence", "pre_selected", "warnings"):
            assert key in entry, f"missing key: {key}"


def test_get_container_options_filtered(tmp_path: Path) -> None:
    """functionality= parameter returns options for that func only."""
    from code_to_module.api import get_container_options

    funcs = [_make_func("align"), _make_func("sort")]
    source = _make_source(tmp_path)
    discovery = _make_discovery(funcs)
    container_opt = _make_container_opt()
    disc_result = ContainerDiscovery(
        tool_name="align",
        options=[container_opt],
    )

    with (
        patch("code_to_module.api.ingest", return_value=source),
        patch("code_to_module.api.discover", return_value=discovery),
        patch("code_to_module.api.assess", return_value=(1, 0.9, [])),
        patch("code_to_module.api.discover_sync", return_value=disc_result),
    ):
        options = get_container_options("script.py", functionality="align")

    assert isinstance(options, list)
    assert len(options) >= 1
    assert "docker_url" in options[0]


def test_functionalities_param_filters(tmp_path: Path) -> None:
    """functionalities='align' → select_functionalities called with that name."""
    from code_to_module.api import convert

    funcs = [_make_func("align"), _make_func("sort")]
    align_only_disc = _make_discovery([funcs[0]])
    source = _make_source(tmp_path)
    spec = _make_spec("align", "ALIGN_RUN")
    test_spec = _make_test_spec()
    container_opt = _make_container_opt()

    with (
        patch("code_to_module.api.ingest", return_value=source),
        patch("code_to_module.api.discover", return_value=_make_discovery(funcs)),
        patch("code_to_module.api.select_functionalities", return_value=align_only_disc) as mock_select,
        patch("code_to_module.api.assess", return_value=(1, 0.9, [])),
        patch("code_to_module.api.resolve", return_value=container_opt),
        patch("code_to_module.api.infer_module_spec_sync", return_value=spec),
        patch("code_to_module.api.generate_test_spec", return_value=test_spec),
        patch("code_to_module.api.generate", return_value=[tmp_path / "main.nf"]),
        patch("code_to_module.api.quick_lint", return_value=[]),
    ):
        result = convert(source="script.py", outdir=str(tmp_path), functionalities="align")

    assert result["success"] is True
    call_kwargs = mock_select.call_args
    assert call_kwargs.kwargs.get("functionalities_flag") == "align"


def test_container_param_overrides(tmp_path: Path) -> None:
    """container='stub' → resolve called with container_flag='stub'."""
    from code_to_module.api import convert

    patches = _full_patches(tmp_path)
    with (
        patches["ingest"],
        patches["discover"],
        patches["select"],
        patches["assess"],
        patch("code_to_module.api.resolve", return_value=_make_container_opt()) as mock_resolve,
        patches["infer"],
        patches["test_gen"],
        patches["generate"],
        patches["lint"],
    ):
        result = convert(source="script.py", outdir=str(tmp_path), container="stub")

    assert result["success"] is True
    call_kwargs = mock_resolve.call_args
    assert call_kwargs.kwargs.get("container_flag") == "stub" or call_kwargs.args[3] == "stub"


def test_cli_calls_api(tmp_path: Path) -> None:
    """cli.py imports from api module — verified via AST parse."""
    cli_path = Path(__file__).parent.parent / "src" / "code_to_module" / "cli.py"
    source_code = cli_path.read_text()
    tree = ast.parse(source_code)

    imports_api = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "api" in alias.name:
                    imports_api = True
        elif isinstance(node, ast.ImportFrom):
            if node.module and "api" in node.module:
                imports_api = True

    # Also accept inline import inside function body
    if not imports_api:
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if hasattr(node, "attr") and node.attr == "api":
                    imports_api = True

    assert imports_api, "cli.py should import from code_to_module.api"
