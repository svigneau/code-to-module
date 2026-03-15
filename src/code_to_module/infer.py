"""LLM-based inference of nf-core ModuleSpec from a FunctionalitySpec."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import anthropic
from rich.console import Console

from code_to_module.models import (
    ChannelSpec,
    CodeSource,
    ContainerSource,
    DetectionMethod,
    ExistingModule,
    FunctionalitySpec,
    ModuleSpec,
)
from code_to_module.standards import get_standards

# Capture at import time so test mocks of the `anthropic` name don't affect the
# except clause (patching `code_to_module.infer.anthropic` replaces the name but
# not this already-bound reference).
_AnthropicAPIError: type[Exception] = anthropic.APIError

_infer_console = Console(stderr=True)

_MODEL = "claude-sonnet-4-20250514"
_MAX_TOKENS = 2000

SYSTEM_PROMPT = """\
You are an expert in nf-core module development (nf-core/tools 3.5+).
Your task is to analyse a script or tool and produce a JSON specification
for a single nf-core Nextflow process module.

Return ONLY valid JSON — no markdown fences, no extra prose.

The JSON must have exactly these top-level keys:

  tool_name          : str   — lowercase tool name (e.g. "samtools", "my_script")
  process_name       : str   — UPPER_SNAKE_CASE (e.g. "SAMTOOLS_SORT", "MY_SCRIPT_RUN")
  functionality_name : str   — the specific subcommand / function being wrapped
  inputs             : list  — list of ChannelSpec objects (see below)
  outputs            : list  — list of ChannelSpec objects (see below)
  label              : str   — one of: process_single, process_medium, process_high,
                               process_high_memory
  ext_args           : str   — always "def args = task.ext.args ?: ''"
  confidence         : float — 0.0–1.0 confidence in this spec
  warnings           : list  — list of strings describing caveats or ambiguities
  conda_package      : str   — conda package name if identifiable, else ""
  script_template    : str   — best-effort shell invocation of the tool, e.g.:
                               "celltypist -i $indata -m $model -o $outdir $args"
                               Use $channel_name for required inputs.
                               Wrap optional inputs:
                               "${gene_file ? \"--gene-file $gene_file\" : \"\"}"
                               Omit version capture — the template adds it.
                               Leave empty ("") only when the command cannot be
                               inferred from the available code and docs.

Each ChannelSpec object has:
  name               : str   — channel name
  type               : str   — "file", "val", or "map"
  description        : str   — human-readable description
  pattern            : str | null — glob pattern for file channels (e.g. "*.bam")
  optional           : bool  — whether the channel is optional
  format_tags        : list  — format tags e.g. ["bam", "sorted_bam"]
  test_data_strategy : str   — "standard_format", "generatable", "custom", or "unknown"

Rules:
- Do NOT include a "versions" output channel — it is added automatically by the module template.
- Input channels follow the nf-core `tuple val(meta), path(files)` pattern.
- Container URLs must NOT be included — they are filled in separately.
- Choose label based on typical resource usage of the tool.
- Set confidence < 0.5 for highly ambiguous or undocumented tools.
- process_name format: TOOLNAME for single-script tools, TOOLNAME_SUBCOMMAND otherwise.
"""


def _build_existing_modules_section(existing_modules: list[ExistingModule]) -> str:
    valid = [m for m in existing_modules if not m.load_error]
    if not valid:
        return ""
    lines = [
        f"- {m.process_name}: inputs={m.inputs}, outputs={m.outputs}"
        for m in valid
    ]
    return "\n".join(lines)


_GENERIC_CLI_STEMS = frozenset(["command_line", "cli", "__main__", "main", "run", "app"])


def _derive_tool_name(source: CodeSource) -> str:
    """Return a meaningful tool name, preferring repo/URL name over generic CLI stems."""
    raw = source.filename
    for suffix in (".py", ".sh", ".R", ".r", ".pl"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
            break
    if raw.lower() in _GENERIC_CLI_STEMS:
        # Prefer repo URL (e.g. https://github.com/Teichlab/celltypist → celltypist)
        if source.url:
            url_stem = source.url.rstrip("/").split("/")[-1].removesuffix(".git")
            if url_stem:
                return url_stem
        # Or repo_root directory name
        if source.repo_root:
            return source.repo_root.name
    return raw


def _build_user_prompt(func: FunctionalitySpec, source: CodeSource, tier: int) -> str:
    tool_name = _derive_tool_name(source)

    parts = [
        f"Tool name: {tool_name}",
        f"Tier: {tier}",
        f"Functionality: {func.name} — {func.description}",
        f"Detection method: {func.detection_method.value}",
    ]

    if func.inferred_inputs or func.inferred_outputs:
        parts += [
            "",
            "Pre-detected I/O from static analysis (use as strong hints):",
            f"  Inputs:  {', '.join(func.inferred_inputs) or '(none)'}",
            f"  Outputs: {', '.join(func.inferred_outputs) or '(none)'}",
        ]

    parts += [
        "",
        "Code section:",
        "```",
        func.code_section,
        "```",
    ]

    existing_section = _build_existing_modules_section(source.existing_modules)
    if existing_section:
        parts += ["", "Existing nf-core modules for reference:", existing_section]

    for ds in source.doc_sources:
        if ds.fetch_error:
            continue
        label = str(ds.url or ds.path)
        parts += ["", f"Documentation ({label}):", ds.content[:2000]]

    return "\n".join(parts)


def _validate_process_name(
    process_name: str, func: FunctionalitySpec, tool_name: str
) -> str:
    upper_tool = tool_name.upper().replace("-", "_")
    upper_func = func.name.upper().replace("-", "_")

    if func.detection_method == DetectionMethod.SINGLE_SCRIPT:
        # Single-script tools: process name is just the tool name, no subcommand
        return upper_tool

    expected = f"{upper_tool}_{upper_func}"
    pn = process_name.upper().replace("-", "_")
    if pn.startswith(upper_tool):
        return pn
    return expected


def _parse_response(text: str, func: FunctionalitySpec) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return dict(json.loads(text))


def _enforce_meta_convention(inputs: list[ChannelSpec]) -> list[ChannelSpec]:
    """Enforce nf-core invariant: the first input must carry the meta map.

    Rules:
    - If the first input is already ``map`` type (→ ``tuple val(meta), path(...)``),
      it is compliant; only strip ``optional=True`` if present.
    - Otherwise, drop any out-of-position channel named "meta" (will be re-added).
    - If a ``map``-typed channel exists but is not first, move it to position 0.
    - If exactly one required ``file`` input remains, promote it to ``map`` type
      (combines with meta into ``tuple val(meta), path(...)``).
    - In all other cases (zero or multiple required paths) prepend a standalone
      ``val(meta)`` channel.
    """
    # Already compliant: first input is map type
    if inputs and inputs[0].type == "map":
        if inputs[0].optional:
            return [inputs[0].model_copy(update={"optional": False})] + list(inputs[1:])
        return list(inputs)

    # Remove any stray "meta"-named channel (may be out-of-position or wrong type)
    without_meta = [ch for ch in inputs if ch.name != "meta"]

    # If any map-typed channel exists but wasn't first, move it to position 0
    map_idx = next((i for i, ch in enumerate(without_meta) if ch.type == "map"), -1)
    if map_idx >= 0:
        map_ch = without_meta[map_idx]
        rest = without_meta[:map_idx] + without_meta[map_idx + 1 :]
        if map_ch.optional:
            map_ch = map_ch.model_copy(update={"optional": False})
        return [map_ch] + rest

    # No map-typed channel: try to promote the single required path to map type
    required_file_indices = [
        i for i, ch in enumerate(without_meta)
        if ch.type == "file" and not ch.optional
    ]
    if len(required_file_indices) == 1:
        result = list(without_meta)
        idx = required_file_indices[0]
        result[idx] = result[idx].model_copy(update={"type": "map", "optional": False})
        return result

    # Multiple required paths (or none): prepend standalone val(meta)
    return [
        ChannelSpec(name="meta", type="val", description="Sample metadata map")
    ] + without_meta


def _apply_meta_invariant(spec: ModuleSpec) -> ModuleSpec:
    """Return a copy of *spec* with meta-convention enforced on inputs."""
    return spec.model_copy(update={"inputs": _enforce_meta_convention(spec.inputs)})


def _json_to_module_spec(data: dict[str, Any], func: FunctionalitySpec, tier: int) -> ModuleSpec:
    tool_name = str(data.get("tool_name", func.name.lower()))
    raw_pn = str(data.get("process_name", f"{tool_name.upper()}_{func.name.upper()}"))
    process_name = _validate_process_name(raw_pn, func, tool_name)

    inputs = [ChannelSpec(**ch) for ch in data.get("inputs", [])]
    outputs = [ChannelSpec(**ch) for ch in data.get("outputs", [])]

    return ModuleSpec(
        tool_name=tool_name,
        process_name=process_name,
        functionality_name=str(data.get("functionality_name", func.name)),
        inputs=inputs,
        outputs=outputs,
        container_docker="",
        container_singularity="",
        container_source=ContainerSource.STUB,
        label=str(data.get("label", get_standards().valid_labels[0])),
        ext_args=str(data.get("ext_args", "def args = task.ext.args ?: ''")),
        script_template=str(data.get("script_template", "")),
        tier=tier,
        confidence=float(data.get("confidence", 0.5)),
        warnings=list(data.get("warnings", [])),
        conda_package=str(data.get("conda_package", "")),
    )


async def infer_module_spec(
    func: FunctionalitySpec,
    source: CodeSource,
    tier: int,
) -> ModuleSpec:
    """Call the Anthropic API to infer a ModuleSpec for one FunctionalitySpec."""
    client = anthropic.AsyncAnthropic()
    user_prompt = _build_user_prompt(func, source, tier)
    spec: ModuleSpec
    try:
        message = await client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = message.content[0].text  # type: ignore[union-attr]
        data = _parse_response(text, func)
        spec = _json_to_module_spec(data, func, tier)
    except (_AnthropicAPIError, TypeError) as e:
        _infer_console.print(f"[yellow]⚠ Anthropic API error: {e}[/yellow]")
        spec = ModuleSpec.tier5_stub(func.name, infer_failed=True)
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        _infer_console.print(f"[yellow]⚠ Failed to parse LLM response: {e}[/yellow]")
        spec = ModuleSpec.tier5_stub(func.name, infer_failed=True)
    except Exception as e:
        _infer_console.print(f"[yellow]⚠ Unexpected inference error: {e}[/yellow]")
        raise
    return _apply_meta_invariant(spec)


def infer_module_spec_sync(
    func: FunctionalitySpec,
    source: CodeSource,
    tier: int,
) -> ModuleSpec:
    """Synchronous wrapper around infer_module_spec."""
    return asyncio.run(infer_module_spec(func, source, tier))


async def infer_all(
    funcs: list[FunctionalitySpec],
    source: CodeSource,
    tiers: list[int],
) -> list[ModuleSpec]:
    """Infer ModuleSpecs for all functionalities in parallel."""
    tasks = [infer_module_spec(func, source, tier) for func, tier in zip(funcs, tiers)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    specs: list[ModuleSpec] = []
    for func, result in zip(funcs, results):
        if isinstance(result, Exception):
            _infer_console.print(f"[yellow]⚠ Inference exception for {func.name}: {result}[/yellow]")
            specs.append(_apply_meta_invariant(ModuleSpec.tier5_stub(func.name, infer_failed=True)))
        else:
            assert isinstance(result, ModuleSpec)
            specs.append(result)
    return specs


def infer_all_sync(
    funcs: list[FunctionalitySpec],
    source: CodeSource,
    tiers: list[int],
) -> list[ModuleSpec]:
    """Synchronous wrapper around infer_all."""
    return asyncio.run(infer_all(funcs, source, tiers))
