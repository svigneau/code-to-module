"""Tests for discover.py."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from code_to_module.discover import discover, select_functionalities
from code_to_module.ingest import ingest
from code_to_module.models import (
    CodeSource,
    DetectionMethod,
    DiscoveryResult,
    DocSource,
    FunctionalitySpec,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _silent_console() -> Console:
    return Console(file=StringIO(), highlight=False)


def _make_result(
    *names: str,
    method: DetectionMethod = DetectionMethod.CLICK_DECORATOR,
    confidence: float = 0.90,
    is_single: bool = False,
) -> DiscoveryResult:
    source = CodeSource(source_type="file", language="python", raw_code="", filename="t.py")
    specs = [
        FunctionalitySpec(
            name=n,
            display_name=n.title(),
            description=n,
            detection_method=method,
            confidence=confidence,
            code_section="",
            pre_selected=confidence >= 0.70,
        )
        for n in names
    ]
    return DiscoveryResult(
        source=source,
        functionalities=specs,
        selected=[],
        detection_method_used=method,
        is_single_functionality=is_single,
    )


def _llm_response(*names: str) -> MagicMock:
    """Build a mock Anthropic response returning the given functionality names."""
    payload = {
        "functionalities": [
            {
                "name": n,
                "display_name": n.title(),
                "description": f"Does {n}",
                "entry_point": n,
                "inferred_inputs": [],
                "inferred_outputs": [],
                "confidence": 0.85,
                "reasoning": "",
            }
            for n in names
        ],
        "is_single_functionality": len(names) == 1,
        "reasoning": "",
    }
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=json.dumps(payload))]
    return mock_resp


# ── console_scripts detector ──────────────────────────────────────────────────


def test_console_scripts_flat_single_spec():
    """Flat console_scripts entry (no subparsers) → exactly 1 spec, no LLM."""
    source = ingest(str(FIXTURES / "repo_console_scripts_flat"))

    with patch("code_to_module.discover.anthropic_module.Anthropic") as MockAnthropic:
        result = discover(source)
        MockAnthropic.assert_not_called()

    assert result.detection_method_used == DetectionMethod.CONSOLE_SCRIPTS
    assert result.is_single_functionality is True
    assert len(result.functionalities) == 1
    spec = result.functionalities[0]
    assert spec.name == "mytool"
    assert spec.detection_method == DetectionMethod.CONSOLE_SCRIPTS
    assert spec.confidence >= 0.90
    # code_section must contain the actual CLI file, not be empty
    assert "click" in spec.code_section
    assert len([ln for ln in spec.code_section.splitlines() if ln.strip()]) >= 5


def test_console_scripts_subparsers_expand():
    """Level 1: console_scripts entry pointing to argparse subparsers → one spec per subcommand."""
    source = ingest(str(FIXTURES / "repo_console_scripts_subparsers"))

    with patch("code_to_module.discover.anthropic_module.Anthropic") as MockAnthropic:
        result = discover(source)
        MockAnthropic.assert_not_called()

    assert len(result.functionalities) >= 2
    names = {s.name for s in result.functionalities}
    assert {"align", "sort"}.issubset(names)
    for spec in result.functionalities:
        assert spec.detection_method == DetectionMethod.ARGPARSE_SUBPARSER
        assert spec.code_section  # not empty


def test_console_scripts_early_exit_modes(tmp_path):
    """Level 2b: early-exit if blocks with file I/O → distinct mode specs."""
    (tmp_path / "setup.py").write_text(
        "import setuptools\n"
        "setuptools.setup(name='biotool', entry_points={'console_scripts': ['biotool=biotool.cli:main']})\n"
    )
    pkg = tmp_path / "biotool"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    # Two early-exit modes: annotate (has file I/O) and a utility (no file I/O, filtered)
    (pkg / "cli.py").write_text(
        "import argparse\n"
        "from pathlib import Path\n\n"
        "def main():\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--annotate', action='store_true')\n"
        "    parser.add_argument('--show-models', action='store_true')\n"
        "    parser.add_argument('--input', type=Path)\n"
        "    parser.add_argument('--output', type=Path)\n"
        "    args = parser.parse_args()\n"
        "    if annotate:\n"
        "        result = run(Path(args.input))\n"
        "        result.write(args.output)\n"
        "        return\n"
        "    if show_models:\n"
        "        print('models')\n"
        "        return\n"
    )
    source = ingest(str(tmp_path))

    with patch("code_to_module.discover.anthropic_module.Anthropic") as MockAnthropic:
        result = discover(source)
        MockAnthropic.assert_not_called()

    names = {s.name for s in result.functionalities}
    # annotate mode has file I/O → included; show_models has none → filtered
    assert "biotool_annotate" in names
    assert "biotool_show_models" not in names


def test_console_scripts_utility_modes_filtered(tmp_path):
    """Level 2b: utility-only early-exit modes (no file I/O) → falls through to Level 3."""
    (tmp_path / "setup.py").write_text(
        "import setuptools\n"
        "setuptools.setup(name='celltypist', entry_points={'console_scripts': ['celltypist=celltypist.cli:main']})\n"
    )
    pkg = tmp_path / "celltypist"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    # Simulate celltypist pattern: update_models and show_models both exit early but
    # have no file I/O → both filtered → Level 3 flat spec
    (pkg / "cli.py").write_text(
        "import click\n\n"
        "@click.command()\n"
        "@click.option('--indata', type=click.Path())\n"
        "@click.option('--update-models', 'update_models', is_flag=True)\n"
        "@click.option('--show-models', 'show_models', is_flag=True)\n"
        "@click.option('--outdir', type=click.Path())\n"
        "def main(indata, update_models, show_models, outdir):\n"
        "    if update_models:\n"
        "        download()\n"
        "        exit(0)\n"
        "    if show_models:\n"
        "        print('models')\n"
        "        exit(0)\n"
        "    run(indata, outdir)\n"
    )
    source = ingest(str(tmp_path))

    with patch("code_to_module.discover.anthropic_module.Anthropic") as MockAnthropic:
        result = discover(source)
        MockAnthropic.assert_not_called()

    # Falls through to Level 3: single flat spec
    assert result.is_single_functionality is True
    assert len(result.functionalities) == 1
    assert result.functionalities[0].name == "celltypist"


def test_console_scripts_llm_never_called_with_docs(tmp_path):
    """LLM must not be called even when doc_sources are present and console_scripts found."""
    # Minimal package with a flat CLI entry point
    (tmp_path / "setup.py").write_text(
        "import setuptools\n"
        "setuptools.setup(name='t', entry_points={'console_scripts': ['t=t.cli:main']})\n"
    )
    pkg = tmp_path / "t"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "cli.py").write_text(
        "import click\n\n"
        "@click.command()\n"
        "@click.option('--x')\n"
        "def main(x): pass\n"
    )
    source = ingest(str(tmp_path))
    source = source.model_copy(
        update={
            "doc_sources": [
                DocSource(
                    url="https://example.com/docs",
                    content="Use t annotate to annotate cells. Use t train to train models.",
                    source_type="url",
                )
            ]
        }
    )

    with patch("code_to_module.discover.anthropic_module.Anthropic") as MockAnthropic:
        result = discover(source)
        MockAnthropic.assert_not_called()

    assert result.is_single_functionality is True
    assert len(result.functionalities) == 1
    assert result.functionalities[0].name == "t"


# ── Rule-based detectors ──────────────────────────────────────────────────────


def test_click_decorators():
    source = ingest(str(FIXTURES / "repo_click_multi"))
    result = discover(source)

    assert result.detection_method_used == DetectionMethod.CLICK_DECORATOR
    assert len(result.functionalities) == 3
    names = {s.name for s in result.functionalities}
    assert names == {"align", "sort", "index"}
    for spec in result.functionalities:
        assert spec.detection_method == DetectionMethod.CLICK_DECORATOR
        assert spec.confidence >= 0.90


def test_argparse_subcommands():
    source = ingest(str(FIXTURES / "repo_argparse_subcommands"))
    result = discover(source)

    assert result.detection_method_used == DetectionMethod.ARGPARSE_SUBPARSER
    assert len(result.functionalities) >= 2
    for spec in result.functionalities:
        assert spec.detection_method == DetectionMethod.ARGPARSE_SUBPARSER


def test_shell_case():
    source = ingest(str(FIXTURES / "repo_shell_case"))
    result = discover(source)

    assert result.detection_method_used == DetectionMethod.SHELL_CASE_STATEMENT
    names = {s.name for s in result.functionalities}
    assert {"align", "sort", "index"}.issubset(names)
    for spec in result.functionalities:
        assert spec.detection_method == DetectionMethod.SHELL_CASE_STATEMENT


def test_multi_scripts():
    source = ingest(str(FIXTURES / "repo_multi_scripts"))
    result = discover(source)

    assert result.detection_method_used == DetectionMethod.MULTI_SCRIPT_REPO
    assert len(result.functionalities) == 3
    for spec in result.functionalities:
        assert spec.detection_method == DetectionMethod.MULTI_SCRIPT_REPO


# ── LLM fallback ─────────────────────────────────────────────────────────────


def test_llm_fallback(tmp_path):
    script = tmp_path / "tool.py"
    script.write_text("def main(): pass\n")
    source = ingest(str(script))

    with patch("code_to_module.discover.anthropic_module.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = _llm_response("tool")
        result = discover(source)

    assert result.is_single_functionality is True
    assert result.detection_method_used == DetectionMethod.SINGLE_SCRIPT
    assert len(result.functionalities) == 1
    assert result.functionalities[0].detection_method == DetectionMethod.SINGLE_SCRIPT


def test_llm_multi(tmp_path):
    script = tmp_path / "pipeline.py"
    script.write_text("def main(): pass\n")
    source = ingest(str(script))

    with patch("code_to_module.discover.anthropic_module.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = _llm_response("align", "sort")
        result = discover(source)

    assert result.detection_method_used == DetectionMethod.LLM_INFERENCE
    assert len(result.functionalities) == 2
    names = {s.name for s in result.functionalities}
    assert names == {"align", "sort"}


# ── Detector priority ─────────────────────────────────────────────────────────


def test_detector_priority(tmp_path):
    """Click detector should fire before multi-script when both would match."""
    # Create a valid click CLI file with ≥2 commands
    cli = tmp_path / "mytool.py"
    cli.write_text(
        "import click\n\n"
        "@click.group()\n"
        "def cli(): pass\n\n"
        "@cli.command()\n"
        "def align():\n"
        "    '''Align reads.'''\n"
        "    pass\n\n"
        "@cli.command()\n"
        "def sort():\n"
        "    '''Sort BAM.'''\n"
        "    pass\n"
    )
    # Also add standalone scripts (would trigger MULTI_SCRIPT_REPO if click didn't fire)
    (tmp_path / "helper_script.py").write_text("def run(): pass\n")
    (tmp_path / "extra.py").write_text("def run(): pass\n")

    source = ingest(str(tmp_path))
    result = discover(source)

    assert result.detection_method_used == DetectionMethod.CLICK_DECORATOR


# ── Selection: Case 1 — single ────────────────────────────────────────────────


def test_single_skips_menu():
    result = _make_result("main", is_single=True)
    console = _silent_console()

    out = select_functionalities(result, None, False, False, console)

    assert len(out.selected) == 1
    assert out.selected[0].name == "main"


# ── Selection: Case 2 — --all-functionalities ─────────────────────────────────


def test_select_all_flag():
    result = _make_result("align", "sort", "index")
    console = _silent_console()

    out = select_functionalities(result, None, all_flag=True, no_interaction=False, console=console)

    assert len(out.selected) == 3


# ── Selection: Case 3 — --functionalities flag ────────────────────────────────


def test_select_functionalities_flag():
    result = _make_result("align", "sort", "index")
    console = _silent_console()

    out = select_functionalities(result, "align,sort", False, False, console)

    assert len(out.selected) == 2
    assert {s.name for s in out.selected} == {"align", "sort"}


def test_unknown_functionality_name_warns():
    result = _make_result("align", "sort")
    buf = StringIO()
    console = Console(file=buf, highlight=False)

    out = select_functionalities(result, "nonexistent", False, False, console)

    assert out.selected == []
    assert "nonexistent" in buf.getvalue()


# ── Selection: Case 4 — no_interaction ───────────────────────────────────────


def test_select_no_interaction():
    result = _make_result("align", "sort", "index")
    # Mark only align as pre_selected
    specs = result.functionalities
    specs[0] = specs[0].model_copy(update={"pre_selected": True})
    specs[1] = specs[1].model_copy(update={"pre_selected": False})
    specs[2] = specs[2].model_copy(update={"pre_selected": False})
    result = result.model_copy(update={"functionalities": specs})

    console = _silent_console()
    out = select_functionalities(result, None, False, no_interaction=True, console=console)

    assert len(out.selected) == 1
    assert out.selected[0].name == "align"


# ── Selection: Case 5 — interactive ──────────────────────────────────────────


def test_select_interactive_valid():
    result = _make_result("align", "sort", "index")
    console = _silent_console()

    with patch("code_to_module.discover._is_tty", return_value=True), patch(
        "builtins.input", return_value="2,3"
    ):
        out = select_functionalities(result, None, False, False, console)

    assert len(out.selected) == 2
    assert {s.name for s in out.selected} == {"sort", "index"}


def test_select_interactive_range():
    result = _make_result("align", "sort", "index")
    console = _silent_console()

    with patch("code_to_module.discover._is_tty", return_value=True), patch(
        "builtins.input", return_value="1-3"
    ):
        out = select_functionalities(result, None, False, False, console)

    assert len(out.selected) == 3
    assert {s.name for s in out.selected} == {"align", "sort", "index"}


# ── Doc sources in LLM prompt ─────────────────────────────────────────────────


def test_docs_included_in_llm_prompt(tmp_path):
    script = tmp_path / "tool.py"
    script.write_text("def main(): pass\n")
    source = ingest(str(script))
    source = source.model_copy(
        update={
            "doc_sources": [
                DocSource(
                    url="https://example.com/docs",
                    content="Use convert_reads to convert and process_reads to process.",
                    source_type="url",
                )
            ]
        }
    )

    captured: dict = {}

    def _fake_create(**kwargs):
        captured["messages"] = kwargs.get("messages", [])
        return _llm_response("convert_reads", "process_reads")

    with patch("code_to_module.discover.anthropic_module.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.side_effect = _fake_create
        discover(source)

    assert captured["messages"], "Anthropic was not called"
    user_content = captured["messages"][0]["content"]
    assert "convert_reads" in user_content or "Use convert" in user_content


def test_docs_absent_from_rule_based():
    """When rule-based fires (click repo), Anthropic should never be called."""
    source = ingest(str(FIXTURES / "repo_click_multi"))
    source = source.model_copy(
        update={
            "doc_sources": [
                DocSource(
                    url="https://example.com/docs",
                    content="Some documentation",
                    source_type="url",
                )
            ]
        }
    )

    with patch("code_to_module.discover.anthropic_module.Anthropic") as MockAnthropic:
        result = discover(source)
        MockAnthropic.assert_not_called()

    assert result.detection_method_used == DetectionMethod.CLICK_DECORATOR


def test_docs_fetch_error_omitted(tmp_path):
    """DocSource with fetch_error should not crash and should be excluded from prompt."""
    script = tmp_path / "tool.py"
    script.write_text("def main(): pass\n")
    source = ingest(str(script))
    source = source.model_copy(
        update={
            "doc_sources": [
                DocSource(
                    url="https://example.com/broken",
                    content="",
                    source_type="url",
                    fetch_error="timed out",
                )
            ]
        }
    )

    captured: dict = {}

    def _fake_create(**kwargs):
        captured["messages"] = kwargs.get("messages", [])
        return _llm_response("a", "b")

    with patch("code_to_module.discover.anthropic_module.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.side_effect = _fake_create
        discover(source)  # must not raise

    if captured.get("messages"):
        user_content = captured["messages"][0]["content"]
        assert "Documentation and tutorials" not in user_content


def test_docs_confidence_bonus(tmp_path):
    """LLM returns 0.80 for 'align' which appears in doc content → final confidence 0.90."""
    script = tmp_path / "tool.py"
    script.write_text("def main(): pass\n")
    source = ingest(str(script))
    source = source.model_copy(
        update={
            "doc_sources": [
                DocSource(
                    url="https://example.com/docs",
                    content="Use align to align reads. Use sort to sort the output.",
                    source_type="url",
                )
            ]
        }
    )

    payload = {
        "functionalities": [
            {
                "name": "align",
                "display_name": "Align",
                "description": "Align reads",
                "entry_point": "align",
                "inferred_inputs": [],
                "inferred_outputs": [],
                "confidence": 0.80,
                "reasoning": "",
            },
            {
                "name": "sort",
                "display_name": "Sort",
                "description": "Sort reads",
                "entry_point": "sort",
                "inferred_inputs": [],
                "inferred_outputs": [],
                "confidence": 0.80,
                "reasoning": "",
            },
        ],
        "is_single_functionality": False,
        "reasoning": "",
    }
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=json.dumps(payload))]

    with patch("code_to_module.discover.anthropic_module.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = mock_resp
        result = discover(source)

    align_spec = next(s for s in result.functionalities if s.name == "align")
    assert align_spec.confidence == pytest.approx(0.90)


# ── Existing module naming pass ───────────────────────────────────────────────


def test_existing_module_naming_pass():
    """FunctionalitySpec named 'sort' gets +0.05 when SAMTOOLS_SORT exists."""
    source = ingest(
        str(FIXTURES / "repo_click_multi"),
        existing_modules=[str(FIXTURES / "modules" / "passing")],
    )
    result = discover(source)

    sort_spec = next(s for s in result.functionalities if s.name == "sort")
    # Original click confidence 0.92 + 0.05 naming bonus = 0.97
    assert sort_spec.confidence == pytest.approx(0.97)
    assert sort_spec.pre_selected is True


def test_existing_module_no_effect_on_rule_based():
    """Naming pass should not change detection_method."""
    source = ingest(
        str(FIXTURES / "repo_click_multi"),
        existing_modules=[str(FIXTURES / "modules" / "passing")],
    )
    result = discover(source)

    assert result.detection_method_used == DetectionMethod.CLICK_DECORATOR
    for spec in result.functionalities:
        assert spec.detection_method == DetectionMethod.CLICK_DECORATOR
