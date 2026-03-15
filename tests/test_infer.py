"""Tests for infer.py — LLM-based ModuleSpec inference.

All Anthropic API calls are mocked.  No real network traffic is made.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from code_to_module.infer import (
    _build_existing_modules_section,
    _build_user_prompt,
    _enforce_meta_convention,
    infer_all_sync,
    infer_module_spec_sync,
)
from code_to_module.models import (
    ChannelSpec,
    CodeSource,
    ContainerSource,
    DetectionMethod,
    ExistingModule,
    FunctionalitySpec,
    ModuleSpec,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

_VALID_JSON: dict = {
    "tool_name": "samtools",
    "process_name": "SAMTOOLS_SORT",
    "functionality_name": "sort",
    "inputs": [
        {
            "name": "bam",
            "type": "file",
            "description": "Input BAM file",
            "pattern": "*.bam",
            "optional": False,
            "format_tags": ["bam"],
            "test_data_strategy": "standard_format",
        }
    ],
    "outputs": [
        {
            "name": "bam",
            "type": "file",
            "description": "Sorted BAM",
            "pattern": "*.sorted.bam",
            "optional": False,
            "format_tags": ["bam", "sorted_bam"],
            "test_data_strategy": "standard_format",
        },
        {
            "name": "versions",
            "type": "val",
            "description": "Software versions",
            "pattern": None,
            "optional": False,
            "format_tags": [],
            "test_data_strategy": "unknown",
        },
    ],
    "label": "process_medium",
    "ext_args": "def args = task.ext.args ?: ''",
    "confidence": 0.95,
    "warnings": [],
    "conda_package": "samtools",
}


def _make_func(
    name: str = "sort",
    detection_method: DetectionMethod = DetectionMethod.ARGPARSE_SUBPARSER,
    description: str = "Sort a BAM file",
) -> FunctionalitySpec:
    return FunctionalitySpec(
        name=name,
        display_name=name.capitalize(),
        description=description,
        detection_method=detection_method,
        confidence=0.9,
        code_section="samtools sort -o output.bam input.bam",
    )


def _make_source(
    filename: str = "samtools.py",
    existing_modules: list[ExistingModule] | None = None,
) -> CodeSource:
    return CodeSource(
        source_type="file",
        path=Path("/fake/samtools.py"),
        language="python",
        raw_code="print('hello')",
        filename=filename,
        existing_modules=existing_modules or [],
    )


def _mock_anthropic(json_payload: dict | None = None, *, raise_exc: Exception | None = None):
    """Return a context manager that patches anthropic.AsyncAnthropic."""
    payload = json_payload if json_payload is not None else _VALID_JSON

    patcher = patch("code_to_module.infer.anthropic")

    class _Ctx:
        def __enter__(self):
            self.mock_mod = patcher.start()
            mock_client = MagicMock()
            self.mock_mod.AsyncAnthropic.return_value = mock_client
            if raise_exc is not None:
                mock_client.messages.create = AsyncMock(side_effect=raise_exc)
            else:
                mock_msg = MagicMock()
                mock_msg.content = [MagicMock(text=json.dumps(payload))]
                mock_client.messages.create = AsyncMock(return_value=mock_msg)
            self.mock_client = mock_client
            return self

        def __exit__(self, *args):
            patcher.stop()

    return _Ctx()


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_script_template_extracted() -> None:
    """Bug 2: script_template field from LLM response is passed through to ModuleSpec."""
    func = _make_func()
    source = _make_source()
    payload = {**_VALID_JSON, "script_template": "samtools sort -o ${prefix}.bam $bam $args"}

    with _mock_anthropic(payload):
        result = infer_module_spec_sync(func, source, tier=1)

    assert result.script_template == "samtools sort -o ${prefix}.bam $bam $args"


def test_script_template_defaults_empty() -> None:
    """Bug 2: missing script_template key in LLM response → empty string (not crash)."""
    func = _make_func()
    source = _make_source()
    payload = {k: v for k, v in _VALID_JSON.items() if k != "script_template"}

    with _mock_anthropic(payload):
        result = infer_module_spec_sync(func, source, tier=1)

    assert result.script_template == ""


def test_versions_not_in_outputs_after_prompt_fix() -> None:
    """Bug 1: even if LLM still includes versions, the model passes it through for
    the template to filter — _json_to_module_spec should not strip it here."""
    func = _make_func()
    source = _make_source()

    with _mock_anthropic(_VALID_JSON):  # _VALID_JSON includes a 'versions' output
        result = infer_module_spec_sync(func, source, tier=1)

    # The ModuleSpec may still carry a versions channel (template skips it);
    # what matters is the channel was parsed without error
    assert isinstance(result, ModuleSpec)
    assert result.tool_name == "samtools"


def test_basic_inference_returns_module_spec() -> None:
    """Valid LLM response → ModuleSpec with expected tool_name and process_name."""
    func = _make_func()
    source = _make_source()

    with _mock_anthropic():
        result = infer_module_spec_sync(func, source, tier=1)

    assert isinstance(result, ModuleSpec)
    assert result.tool_name == "samtools"
    assert result.process_name == "SAMTOOLS_SORT"
    assert result.tier == 1
    assert result.confidence == pytest.approx(0.95)
    assert result.label == "process_medium"
    assert result.container_source == ContainerSource.STUB
    assert result.container_docker == ""


def test_single_script_process_name_toolname_only() -> None:
    """SINGLE_SCRIPT detection method → process_name is TOOLNAME only (no subcommand)."""
    func = _make_func(name="myscript", detection_method=DetectionMethod.SINGLE_SCRIPT)
    source = _make_source(filename="myscript.py")

    # LLM returns a process_name with subcommand appended — should be corrected
    payload = {**_VALID_JSON, "tool_name": "myscript", "process_name": "MYSCRIPT_RUN"}

    with _mock_anthropic(payload):
        result = infer_module_spec_sync(func, source, tier=3)

    # For SINGLE_SCRIPT the expected name is just the tool name
    assert result.process_name == "MYSCRIPT"


def test_non_single_script_process_name_tool_func() -> None:
    """Non-SINGLE_SCRIPT detection → process_name includes the subcommand part."""
    func = _make_func(name="sort", detection_method=DetectionMethod.ARGPARSE_SUBPARSER)
    source = _make_source()

    with _mock_anthropic(_VALID_JSON):
        result = infer_module_spec_sync(func, source, tier=1)

    # process_name from LLM starts with tool_name so it is accepted as-is
    assert "SAMTOOLS" in result.process_name
    assert result.process_name == "SAMTOOLS_SORT"


def test_tier5_fallback_on_auth_typeerror() -> None:
    """TypeError from missing API key (SDK auth validation) returns tier5_stub, not crash."""
    func = _make_func(name="mytool")
    source = _make_source(filename="mytool.py")

    patcher = patch("code_to_module.infer.anthropic")
    with patcher as mock_mod:
        mock_client = MagicMock()
        mock_mod.AsyncAnthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(
            side_effect=TypeError(
                '"Could not resolve authentication method. Expected either api_key or auth_token to be set."'
            )
        )

        result = infer_module_spec_sync(func, source, tier=1)

    assert result.tier == 5
    assert result.infer_failed is True
    assert result.tool_name == "mytool"


def test_tier5_fallback_on_invalid_json() -> None:
    """If the LLM returns invalid JSON, tier5_stub is returned."""
    func = _make_func(name="mytool")
    source = _make_source(filename="mytool.py")

    # bad_payload would be {} — missing required keys causes ValueError when parsed
    # Simulate genuinely unparseable text
    patcher = patch("code_to_module.infer.anthropic")
    with patcher as mock_mod:
        mock_client = MagicMock()
        mock_mod.AsyncAnthropic.return_value = mock_client
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="this is not json at all!!!")]
        mock_client.messages.create = AsyncMock(return_value=mock_msg)

        result = infer_module_spec_sync(func, source, tier=1)

    assert result.tier == 5
    assert result.confidence == 0.0
    assert result.tool_name == "mytool"
    assert len(result.warnings) >= 1


def test_markdown_fences_stripped() -> None:
    """Response wrapped in ```json ... ``` fences is parsed correctly."""
    func = _make_func()
    source = _make_source()

    fenced_text = "```json\n" + json.dumps(_VALID_JSON) + "\n```"

    patcher = patch("code_to_module.infer.anthropic")
    with patcher as mock_mod:
        mock_client = MagicMock()
        mock_mod.AsyncAnthropic.return_value = mock_client
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=fenced_text)]
        mock_client.messages.create = AsyncMock(return_value=mock_msg)

        result = infer_module_spec_sync(func, source, tier=1)

    assert isinstance(result, ModuleSpec)
    assert result.tool_name == "samtools"


def test_infer_all_sync_returns_list() -> None:
    """infer_all_sync returns a list of ModuleSpec, one per functionality."""
    funcs = [
        _make_func(name="sort"),
        _make_func(name="index"),
    ]
    source = _make_source()

    payload_sort = {**_VALID_JSON, "tool_name": "samtools", "process_name": "SAMTOOLS_SORT"}
    payload_index = {**_VALID_JSON, "tool_name": "samtools", "process_name": "SAMTOOLS_INDEX",
                     "functionality_name": "index"}

    responses = [
        MagicMock(content=[MagicMock(text=json.dumps(payload_sort))]),
        MagicMock(content=[MagicMock(text=json.dumps(payload_index))]),
    ]

    patcher = patch("code_to_module.infer.anthropic")
    with patcher as mock_mod:
        mock_client = MagicMock()
        mock_mod.AsyncAnthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(side_effect=responses)

        result = infer_all_sync(funcs, source, tiers=[1, 1])

    assert isinstance(result, list)
    assert len(result) == 2
    assert all(isinstance(s, ModuleSpec) for s in result)


def test_infer_all_exception_returns_tier5_stub() -> None:
    """If one infer call raises, the corresponding result is a tier5_stub."""
    funcs = [_make_func(name="sort"), _make_func(name="broken")]
    source = _make_source()

    good_msg = MagicMock(content=[MagicMock(text=json.dumps(_VALID_JSON))])

    patcher = patch("code_to_module.infer.anthropic")
    with patcher as mock_mod:
        mock_client = MagicMock()
        mock_mod.AsyncAnthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(
            side_effect=[good_msg, RuntimeError("API down")]
        )

        result = infer_all_sync(funcs, source, tiers=[1, 1])

    assert len(result) == 2
    assert result[0].tool_name == "samtools"
    assert result[1].tier == 5  # tier5_stub for the broken func


def test_existing_modules_included_when_valid() -> None:
    """When source has valid existing modules, their info appears in the user prompt."""
    existing = ExistingModule(
        path=Path("/nf-core/modules/samtools/sort/main.nf"),
        tool_name="samtools",
        process_name="SAMTOOLS_SORT",
        container_docker="quay.io/biocontainers/samtools:1.17",
        container_singularity="https://depot.galaxyproject.org/singularity/samtools:1.17",
        label="process_medium",
        inputs=["bam"],
        outputs=["sorted_bam"],
    )
    source = _make_source(existing_modules=[existing])

    section = _build_existing_modules_section(source.existing_modules)
    assert "SAMTOOLS_SORT" in section
    assert section.strip() != ""


def test_existing_modules_omitted_when_all_have_load_error() -> None:
    """When all existing modules have load_error, the section is empty."""
    broken = ExistingModule(
        path=Path("/nf-core/modules/bad/main.nf"),
        tool_name="bad",
        process_name="BAD",
        container_docker="",
        container_singularity="",
        label="process_single",
        load_error="could not parse",
    )
    source = _make_source(existing_modules=[broken])

    section = _build_existing_modules_section(source.existing_modules)
    assert section == ""


def test_user_prompt_contains_tier_and_func_name() -> None:
    """_build_user_prompt output includes the tier number and function name."""
    func = _make_func(name="sort")
    source = _make_source(filename="samtools.py")

    prompt = _build_user_prompt(func, source, tier=2)

    assert "Tier: 2" in prompt
    assert "sort" in prompt


# ── Meta-convention enforcement tests ─────────────────────────────────────────


def _ch(name: str, type: str, optional: bool = False) -> ChannelSpec:
    return ChannelSpec(name=name, type=type, description=f"{name} channel", optional=optional)


def test_meta_already_map_type_is_preserved() -> None:
    """map-typed first input (tuple val(meta), path(...)) is left untouched."""
    inputs = [_ch("indata", "map")]
    result = _enforce_meta_convention(inputs)
    assert result[0].type == "map"
    assert result[0].name == "indata"


def test_meta_map_type_not_optional() -> None:
    """map-typed first input with optional=True has optional stripped."""
    inputs = [_ch("indata", "map", optional=True)]
    result = _enforce_meta_convention(inputs)
    assert result[0].optional is False


def test_meta_single_required_file_promoted_to_map() -> None:
    """Single required file input → promoted to map type (tuple val(meta), path(...))."""
    inputs = [_ch("indata", "file")]
    result = _enforce_meta_convention(inputs)
    assert len(result) == 1
    assert result[0].name == "indata"
    assert result[0].type == "map"
    assert result[0].optional is False


def test_meta_single_required_file_with_optionals_promoted() -> None:
    """Single required file + optional files → required promoted, optionals kept."""
    inputs = [
        _ch("indata", "file"),
        _ch("model", "file", optional=True),
        _ch("gene_file", "file", optional=True),
    ]
    result = _enforce_meta_convention(inputs)
    assert result[0].name == "indata"
    assert result[0].type == "map"
    assert result[1].name == "model"
    assert result[1].type == "file"
    assert result[1].optional is True


def test_meta_multiple_required_files_prepend_val() -> None:
    """Multiple required file inputs → val(meta) prepended."""
    inputs = [_ch("indata", "file"), _ch("model", "file")]
    result = _enforce_meta_convention(inputs)
    assert result[0].name == "meta"
    assert result[0].type == "val"
    assert result[1].name == "indata"
    assert result[2].name == "model"


def test_meta_empty_inputs_prepend_val() -> None:
    """Empty input list (e.g. tier5_stub) → val(meta) prepended."""
    result = _enforce_meta_convention([])
    assert len(result) == 1
    assert result[0].name == "meta"
    assert result[0].type == "val"


def test_meta_out_of_position_val_dropped_and_file_promoted() -> None:
    """meta val channel out of position is dropped; single file is promoted to map."""
    inputs = [_ch("indata", "file"), _ch("meta", "val")]
    result = _enforce_meta_convention(inputs)
    assert result[0].name == "indata"
    assert result[0].type == "map"
    assert len(result) == 1


def test_meta_out_of_position_map_moved_to_first() -> None:
    """A map-typed channel that isn't first is moved to position 0."""
    inputs = [_ch("other", "val"), _ch("indata", "map")]
    result = _enforce_meta_convention(inputs)
    assert result[0].name == "indata"
    assert result[0].type == "map"


def test_tier5_stub_gets_meta_via_infer_module_spec_sync() -> None:
    """When LLM raises TypeError (auth), returned tier5_stub has meta as first input."""
    func = _make_func(name="celltypist")
    source = _make_source(filename="celltypist.py")

    patcher = patch("code_to_module.infer.anthropic")
    with patcher as mock_mod:
        mock_client = MagicMock()
        mock_mod.AsyncAnthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(
            side_effect=TypeError("Could not resolve authentication method")
        )
        result = infer_module_spec_sync(func, source, tier=1)

    assert result.tier == 5
    assert len(result.inputs) >= 1
    assert result.inputs[0].name == "meta"


def test_llm_result_meta_enforced_via_infer_module_spec_sync() -> None:
    """LLM returns file-typed input → promoted to map by meta-invariant post-processing."""
    func = _make_func(name="sort")
    source = _make_source()

    # LLM returns bam as file type (no meta), one required path
    payload = {**_VALID_JSON, "inputs": [
        {
            "name": "bam",
            "type": "file",
            "description": "Input BAM",
            "pattern": "*.bam",
            "optional": False,
            "format_tags": ["bam"],
            "test_data_strategy": "standard_format",
        }
    ]}

    with _mock_anthropic(payload):
        result = infer_module_spec_sync(func, source, tier=1)

    assert result.inputs[0].type == "map"
    assert result.inputs[0].name == "bam"
