"""LLM contract tests — verify post-processing invariants hold on real API output.

All tests require ANTHROPIC_API_KEY and are skipped automatically when it is
absent.  Run with:
    ANTHROPIC_API_KEY=... pytest -m llm -v
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from code_to_module.assess import assess
from code_to_module.discover import discover
from code_to_module.generate import render_module
from code_to_module.infer import infer_module_spec_sync
from code_to_module.ingest import ingest
from code_to_module.models import ModuleSpec

pytestmark = pytest.mark.llm

_FIXTURE = Path("tests/fixtures/modules/chain_input")


@pytest.fixture(autouse=True)
def require_api_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — skipping LLM contract tests")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _source():
    return ingest(str(_FIXTURE))


def _functionalities():
    source = _source()
    result = discover(source)
    return source, result.functionalities


def _infer_spec() -> ModuleSpec:
    source, funcs = _functionalities()
    assert funcs, "Discovery returned nothing — chain test prerequisite failed"
    func = funcs[0]
    tier, _, _ = assess(func, source, console=_null_console())
    return infer_module_spec_sync(func, source, tier)


def _null_console():
    from rich.console import Console

    return Console(file=io.StringIO())


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_infer_returns_module_spec() -> None:
    """infer() returns a valid ModuleSpec for the chaintool fixture."""
    spec = _infer_spec()
    assert isinstance(spec, ModuleSpec)


def test_meta_is_first_input() -> None:
    """LLM output always has a map-typed first input (carries meta) after post-processing.

    The channel may be named 'reads', 'input', etc. — _enforce_meta_convention
    promotes a single required file to type='map' (tuple val(meta), path(...)).
    We assert type=='map', not name=='meta', because the name comes from the LLM.
    """
    spec = _infer_spec()
    assert spec.inputs, "No inputs in spec"
    assert spec.inputs[0].type == "map", (
        f"First input type is '{spec.inputs[0].type}', expected 'map' (carries meta) — "
        "check meta enforcement post-processing in infer.py"
    )


def test_no_duplicate_versions_emit() -> None:
    """Generated main.nf has exactly one versions emit."""
    spec = _infer_spec()
    main_nf = render_module(spec)
    count = main_nf.count("emit: versions")
    assert count == 1, (
        f"Found {count} versions emits in generated main.nf — "
        "check duplicate-versions guard in generate.py"
    )


def test_no_todo_in_script_block() -> None:
    """Script block contains no TODO when LLM provides a script_template.

    script_template is a best-effort field — the LLM may omit it for ambiguous
    tools.  When it is empty the template renders a // TODO comment, which is
    expected and correct.  We skip rather than fail in that case so the test
    only fires when the guard can actually enforce the invariant.

    Container block TODO URLs are expected (container is resolved by api.convert,
    not by infer_module_spec_sync), so we extract only the triple-quoted script
    block content before checking.
    """
    spec = _infer_spec()
    if not spec.script_template:
        pytest.skip("LLM returned empty script_template (best-effort field) — TODO placeholder is expected")

    main_nf = render_module(spec)

    # Extract the triple-quoted content of the script: block using plain string ops
    # to avoid regex headaches with triple-quote delimiters.
    marker = "\n    script:"
    idx = main_nf.find(marker)
    if idx != -1:
        idx_open = main_nf.find('"""', idx)
        idx_close = main_nf.find('"""', idx_open + 3) if idx_open != -1 else -1
        script_block = main_nf[idx_open + 3 : idx_close] if idx_close != -1 else main_nf
    else:
        script_block = main_nf

    assert "TODO" not in script_block, (
        "LLM left a TODO placeholder in the script block — "
        "check script_template post-processing in infer.py"
    )


def test_ext_args_present() -> None:
    """Generated script block uses task.ext.args."""
    spec = _infer_spec()
    main_nf = render_module(spec)
    assert "task.ext.args" in main_nf, (
        "Generated module does not use task.ext.args — "
        "check ext_args enforcement in infer.py or generate.py template"
    )


def test_version_capture_present() -> None:
    """Generated module captures the tool version via the nf-core 3.5+ topic channel.

    nf-core 3.5+ uses env(TOOL_VERSION) + topic: 'versions' instead of writing
    versions.yml directly.  We check that TOOL_VERSION is captured in the script
    block and emitted via the topic channel.
    """
    spec = _infer_spec()
    main_nf = render_module(spec)
    assert "TOOL_VERSION" in main_nf, (
        "Generated module does not capture TOOL_VERSION — "
        "check the script block template in main.nf.j2"
    )
    assert "topic: 'versions'" in main_nf, (
        "Generated module is missing the topic channel emit for versions — "
        "check the output block template in main.nf.j2"
    )
