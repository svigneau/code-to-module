"""Clean public API for code-to-module.

This module is the stable interface for programmatic use.
It produces NO Rich output — all results are returned as plain dicts.

Usage:
    from code_to_module import convert, get_functionalities, get_container_options
"""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from code_to_module.assess import assess
from code_to_module.container import discover_sync, resolve
from code_to_module.discover import discover, select_functionalities
from code_to_module.generate import generate
from code_to_module.infer import infer_module_spec_sync
from code_to_module.ingest import ingest
from code_to_module.models import ModuleSpec
from code_to_module.quick_lint import quick_lint
from code_to_module.standards import get_standards
from code_to_module.test_gen import generate_test_spec

# A console that discards all output — used internally so nothing is printed.
_NULL_CONSOLE = Console(file=io.StringIO(), highlight=False)


def _null_console() -> Console:
    """Return a fresh no-op console (separate StringIO per call for thread safety)."""
    return Console(file=io.StringIO(), highlight=False)


def convert(
    source: str,
    outdir: str | Path,
    tier_override: int | None = None,
    container: str | None = None,
    functionalities: str | None = None,
    dry_run: bool = False,
    no_lint: bool = False,
    docs: list[str] | None = None,
    existing_modules: list[str] | None = None,
    _inject_spec: ModuleSpec | None = None,
) -> dict:
    """Convert *source* into nf-core module(s) and return structured results.

    Parameters
    ----------
    source:
        Local file path, directory, or Git URL.
    outdir:
        Directory to write generated modules into.
    tier_override:
        Override complexity tier (1–5) for all functionalities.
    container:
        Override container strategy: "dockerfile" | "biocontainers" | "generate" | "stub".
    functionalities:
        Comma-separated functionality names to generate, or None for all.
    dry_run:
        If True, skip file generation and return empty files_created lists.
    docs:
        Optional list of documentation URLs or local paths.
    existing_modules:
        Optional list of existing nf-core module directory paths for consistency checks.
    _inject_spec:
        Test seam: when not None, skip container resolution and LLM inference and use
        this spec directly.  Container and tier fields from the injected spec are used
        as-is.  Intended only for unit/integration tests.

    Returns
    -------
    dict with keys:
        - 'success': bool
        - 'modules': list[dict]   one entry per generated module
        - 'functionalities_found': list[str]
        - 'functionalities_selected': list[str]
        - 'detection_method': str
        - 'error': str | None
    """
    console = _null_console()
    outdir_path = Path(outdir)
    standards = get_standards()

    try:
        # 1. Ingest
        code_source = ingest(
            source,
            docs=docs or [],
            existing_modules=existing_modules or [],
        )

        # 2. Discover
        disc_result = discover(code_source)

        # 3. Select (non-interactive — API never prompts)
        disc_result = select_functionalities(
            disc_result,
            functionalities_flag=functionalities,
            all_flag=(functionalities is None),
            no_interaction=True,
            console=console,
        )

        functionalities_found = [f.name for f in disc_result.functionalities]
        functionalities_selected = [f.name for f in disc_result.selected]
        detection_method = disc_result.detection_method_used.value

        if dry_run:
            return {
                "success": True,
                "modules": [
                    {
                        "functionality_name": f.name,
                        "process_name": "",
                        "tier": tier_override or 0,
                        "confidence": f.confidence,
                        "container_source": "",
                        "container_docker": "",
                        "test_data_strategies": {},
                        "needs_derivation": False,
                        "files_created": [],
                        "warnings": [],
                        "module_spec": {},
                    }
                    for f in disc_result.selected
                ],
                "functionalities_found": functionalities_found,
                "functionalities_selected": functionalities_selected,
                "detection_method": detection_method,
                "error": None,
            }

        modules: list[dict] = []

        for func in disc_result.selected:
            # 4. Assess
            if tier_override is not None:
                func_tier = tier_override
                func_confidence = func.confidence
                tier_warnings: list[str] = []
            else:
                func_tier, func_confidence, tier_warnings = assess(func, code_source, None)

            if func_tier == 5:
                # Skip Tier 5 — not generatable
                modules.append({
                    "functionality_name": func.name,
                    "process_name": "",
                    "tier": 5,
                    "confidence": func_confidence,
                    "container_source": "",
                    "container_docker": "",
                    "test_data_strategies": {},
                    "needs_derivation": False,
                    "files_created": [],
                    "warnings": tier_warnings + ["Tier 5: requires manual module creation."],
                    "module_spec": {},
                })
                continue

            if _inject_spec is not None:
                # Test seam: skip container resolution and LLM inference entirely.
                spec = _inject_spec
            else:
                # 5. Container resolution (non-interactive)
                container_opt = resolve(
                    code_source,
                    func.name,
                    func_tier,
                    container,
                    no_interaction=True,
                    console=console,
                )

                # 6. LLM inference
                spec = infer_module_spec_sync(func, code_source, func_tier)
                spec = spec.model_copy(update={
                    "container_docker": container_opt.docker_url,
                    "container_singularity": container_opt.singularity_url,
                    "container_source": container_opt.source,
                    "dockerfile_content": container_opt.dockerfile_content,
                    "tier": func_tier,
                })

            # 7. Test data strategy
            test_spec = generate_test_spec(spec, code_source, standards, console)

            # 8. Generate files
            created_paths = generate(
                spec, test_spec, outdir_path, code_source.existing_modules, console
            )

            # 9. Quick lint (collect warnings, don't display)
            if no_lint:
                lint_warnings = []
            else:
                tool_dir = outdir_path / spec.tool_name
                lint_warnings = quick_lint(tool_dir, standards)
            warning_strs = tier_warnings + [
                f"[{w.severity}] {w.check}: {w.message}" for w in lint_warnings
            ]

            # Build test_data_strategies from inferred inputs
            test_data_strategies = {
                ch.name: ch.test_data_strategy
                for ch in spec.inputs
            }

            modules.append({
                "functionality_name": func.name,
                "process_name": spec.process_name,
                "tier": spec.tier,
                "confidence": func_confidence,
                "container_source": spec.container_source.value,
                "container_docker": spec.container_docker,
                "test_data_strategies": test_data_strategies,
                "needs_derivation": test_spec.needs_derivation,
                "files_created": [str(p) for p in created_paths],
                "warnings": warning_strs,
                "module_spec": spec.model_dump(mode="json"),
            })

        return {
            "success": True,
            "modules": modules,
            "functionalities_found": functionalities_found,
            "functionalities_selected": functionalities_selected,
            "detection_method": detection_method,
            "error": None,
        }

    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "modules": [],
            "functionalities_found": [],
            "functionalities_selected": [],
            "detection_method": "",
            "error": str(exc),
        }


def get_functionalities(
    source: str,
    docs: list[str] | None = None,
    existing_modules: list[str] | None = None,
) -> list[dict]:
    """Return all detected functionalities with their metadata.

    Useful for any caller that wants to present or filter the selection
    before calling convert() — a GUI, a CI script, or a companion tool.
    """
    code_source = ingest(source, docs=docs or [], existing_modules=existing_modules or [])
    result = discover(code_source)
    return [
        {
            "name": f.name,
            "display_name": f.display_name,
            "description": f.description,
            "detection_method": f.detection_method.value,
            "confidence": f.confidence,
            "inferred_inputs": f.inferred_inputs,
            "inferred_outputs": f.inferred_outputs,
            "pre_selected": f.pre_selected,
            "warnings": f.warnings,
        }
        for f in result.functionalities
    ]


def get_container_options(
    source: str,
    functionality: str | None = None,
    docs: list[str] | None = None,
    existing_modules: list[str] | None = None,
) -> list[dict]:
    """Return all available container options for the named functionality.

    If *functionality* is None, uses the first detected functionality.
    Useful for GUIs, CI reports, or any caller that wants to inspect options
    before calling convert().
    """
    code_source = ingest(source, docs=docs or [], existing_modules=existing_modules or [])
    disc_result = discover(code_source)

    if not disc_result.functionalities:
        return []

    func = next(
        (f for f in disc_result.functionalities if f.name == functionality),
        disc_result.functionalities[0],
    )

    tier, _, _ = assess(func, code_source, None)
    container_discovery = discover_sync(code_source, func.name, tier)
    return [opt.model_dump(mode="json") for opt in container_discovery.options]
