"""Assess the complexity tier of a detected functionality."""

from __future__ import annotations

import re

import httpx
from rich.console import Console
from rich.panel import Panel

from code_to_module.models import CodeSource, DetectionMethod, FunctionalitySpec
from code_to_module.standards import get_standards

_ANACONDA_API = "https://api.anaconda.org/package/bioconda/{tool}"
_BIOTOOLS_API = "https://bio.tools/api/tool/{tool}/"

_ARGUMENT_PARSING_RE = re.compile(
    r"argparse|ArgumentParser|add_argument|click\.option|click\.argument"
    r"|optparse|OptionParser",
    re.IGNORECASE,
)
_IO_VARIABLE_RE = re.compile(
    r"\b(?:input|output)(?:_?file|_?path|_?dir)\b",
    re.IGNORECASE,
)
_OPTIONAL_FLAG_RE = re.compile(
    r"nargs\s*=\s*['\"]?\?['\"]?|default\s*=\s*None|required\s*=\s*False",
)
_OUTPUT_PATTERN_RE = re.compile(r"-o\b|--output\b|\bstdout\b|\bsys\.stdout\b|>\s*\w")

_HIGH_CONFIDENCE_METHODS = frozenset({
    DetectionMethod.CLICK_DECORATOR,
    DetectionMethod.ARGPARSE_SUBPARSER,
    DetectionMethod.SHELL_CASE_STATEMENT,
})

# Per-tool result caches (populated lazily, avoids repeated HTTP calls)
_bioconda_cache: dict[str, bool] = {}
_biotools_cache: dict[str, bool] = {}


# ── Internal helpers ───────────────────────────────────────────────────────────


def _extract_tool_calls(code: str, known_tools: list[str]) -> list[str]:
    """Return known tool names that appear as whole words in code."""
    return [t for t in known_tools if re.search(rf"\b{re.escape(t)}\b", code)]


def _check_bioconda(tool: str) -> bool:
    """Return True if tool has a Bioconda package (cached per tool name)."""
    if tool not in _bioconda_cache:
        try:
            resp = httpx.get(
                _ANACONDA_API.format(tool=tool), timeout=5.0, follow_redirects=True
            )
            _bioconda_cache[tool] = resp.status_code == 200
        except Exception:
            _bioconda_cache[tool] = False
    return _bioconda_cache[tool]


def _check_biotools(tool: str) -> bool:
    """Return True if tool is in the bio.tools registry (cached per tool name)."""
    if tool not in _biotools_cache:
        try:
            resp = httpx.get(
                _BIOTOOLS_API.format(tool=tool), timeout=5.0, follow_redirects=True
            )
            _biotools_cache[tool] = resp.status_code == 200
        except Exception:
            _biotools_cache[tool] = False
    return _biotools_cache[tool]


def _count_output_patterns(code: str) -> int:
    return len(_OUTPUT_PATTERN_RE.findall(code))


def _has_argument_parsing(code: str) -> bool:
    return bool(_ARGUMENT_PARSING_RE.search(code))


def _has_io_variables(code: str) -> bool:
    return bool(_IO_VARIABLE_RE.search(code))


def _tier_color(tier: int) -> str:
    return {1: "green", 2: "green", 3: "yellow", 4: "orange1", 5: "red"}.get(
        tier, "white"
    )


def _print_panel(
    console: Console,
    func: FunctionalitySpec,
    tier: int,
    confidence: float,
    warnings: list[str],
) -> None:
    color = _tier_color(tier)
    lines = [f"[{color}]Tier {tier}[/{color}]  Confidence: {confidence * 100:.0f}%"]
    for w in warnings:
        lines.append(f"[yellow]⚠ {w}[/yellow]")
    console.print(
        Panel(
            "\n".join(lines),
            title=f"Assessing: {func.display_name} [{func.detection_method.value}]",
            border_style=color,
        )
    )


# ── Public API ─────────────────────────────────────────────────────────────────


def assess(
    func: FunctionalitySpec,
    source: CodeSource,
    console: Console | None = None,
) -> tuple[int, float, list[str]]:
    """Assess the complexity tier of a single functionality.

    Returns (tier, confidence, warnings).  Tiers 1–5 per CLAUDE.md spec.
    Always reads known_tools from get_standards() — never uses a hardcoded list.
    """
    if console is None:
        console = Console()

    known_tools = get_standards().known_tools
    language = source.language
    code = func.code_section
    lines = [ln for ln in code.splitlines() if ln.strip()]
    warnings: list[str] = []

    # ── Tier 5: cannot proceed ────────────────────────────────────────────────
    reasons5: list[str] = []
    if language == "unknown":
        reasons5.append("language is unknown")
    if len(lines) < 5:
        reasons5.append(f"code section is too short ({len(lines)} non-blank lines)")
    if func.confidence == 0.0:
        reasons5.append("detection confidence is 0.0")
    if reasons5:
        warnings.append(f"Cannot proceed: {'; '.join(reasons5)}")
        _print_panel(console, func, 5, 0.0, warnings)
        return 5, 0.0, warnings

    # ── Tool call analysis ────────────────────────────────────────────────────
    tools_found = _extract_tool_calls(code, known_tools)

    # ── Tier 1 / Tier 2 (CONSOLE_SCRIPTS path) ───────────────────────────────
    # For packages with an explicit console_scripts entry, func.name IS the CLI
    # tool name (e.g. "celltypist"). The code section is its Python implementation,
    # so tools_found will be empty (no subprocess calls). Check Bioconda directly
    # using func.name before the generic tools_found path.
    if (
        func.detection_method == DetectionMethod.CONSOLE_SCRIPTS
        and language in {"bash", "python"}
        and _check_bioconda(func.name)
    ):
        output_count = _count_output_patterns(code)
        has_optional = bool(_OPTIONAL_FLAG_RE.search(code))
        in_biotools = _check_biotools(func.name)

        complex_reasons: list[str] = []
        if output_count > 1:
            complex_reasons.append(
                f"multiple output patterns detected ({output_count})"
            )
        if has_optional:
            complex_reasons.append("optional flags present")
        if in_biotools:
            complex_reasons.append(f"'{func.name}' is in bio.tools registry")

        if not complex_reasons:
            conf = max(0.90, func.confidence)
            _print_panel(console, func, 1, conf, [])
            return 1, conf, []
        else:
            warnings.extend(complex_reasons)
            conf = max(0.75, min(func.confidence, 0.89))
            _print_panel(console, func, 2, conf, warnings)
            return 2, conf, warnings

    # ── Tier 1 / Tier 2: single known tool with Bioconda entry ───────────────
    if language in {"bash", "python"} and len(tools_found) == 1:
        tool = tools_found[0]
        if _check_bioconda(tool):
            if func.detection_method in _HIGH_CONFIDENCE_METHODS:
                output_count = _count_output_patterns(code)
                has_optional = bool(_OPTIONAL_FLAG_RE.search(code))
                in_biotools = _check_biotools(tool)

                tool_complex_reasons: list[str] = []
                if output_count > 1:
                    tool_complex_reasons.append(
                        f"multiple output patterns detected ({output_count})"
                    )
                if has_optional:
                    tool_complex_reasons.append("optional flags present")
                if in_biotools:
                    tool_complex_reasons.append(f"'{tool}' is in bio.tools registry")

                if not tool_complex_reasons:
                    # Tier 1
                    conf = max(0.90, func.confidence)
                    _print_panel(console, func, 1, conf, [])
                    return 1, conf, []
                else:
                    # Tier 2 (complexity present)
                    warnings.extend(tool_complex_reasons)
                    conf = max(0.75, min(func.confidence, 0.89))
                    _print_panel(console, func, 2, conf, warnings)
                    return 2, conf, warnings

            elif func.detection_method == DetectionMethod.MULTI_SCRIPT_REPO:
                conf = max(0.75, min(func.confidence, 0.89))
                _print_panel(console, func, 2, conf, [])
                return 2, conf, []

    # ── Tier 2 alternative: MULTI_SCRIPT_REPO + any known tool in Bioconda ───
    if (
        func.detection_method == DetectionMethod.MULTI_SCRIPT_REPO
        and tools_found
        and any(_check_bioconda(t) for t in tools_found)
    ):
        conf = max(0.75, min(func.confidence, 0.89))
        _print_panel(console, func, 2, conf, [])
        return 2, conf, []

    # ── Tier 4 triggers (checked before Tier 3 to catch hard cases first) ────
    tier4_reasons: list[str] = []
    if len(tools_found) > 1:
        tier4_reasons.append(
            f"multiple external tools detected: {', '.join(tools_found)}"
        )
    if len(lines) > 500:
        tier4_reasons.append(f"code section is very long ({len(lines)} lines)")
    if (
        func.detection_method == DetectionMethod.LLM_INFERENCE
        and func.confidence < 0.65
    ):
        tier4_reasons.append(
            f"LLM-inferred functionality with low confidence ({func.confidence:.2f})"
        )
    if not _has_argument_parsing(code) and not _has_io_variables(code):
        tier4_reasons.append("no clear argument parsing or I/O variables detected")

    if tier4_reasons:
        warnings.extend(tier4_reasons)
        conf = max(0.25, min(func.confidence, 0.49))
        _print_panel(console, func, 4, conf, warnings)
        return 4, conf, warnings

    # ── Tier 3: custom script with inferrable I/O ─────────────────────────────
    if language in {"python", "R", "perl"}:
        if _has_argument_parsing(code) or _has_io_variables(code):
            warnings.append("No container found — stub will be generated")
            conf = max(0.50, min(func.confidence, 0.74))
            _print_panel(console, func, 3, conf, warnings)
            return 3, conf, warnings

    # ── Default: Tier 4 ───────────────────────────────────────────────────────
    warnings.append("Could not classify — defaulting to Tier 4")
    conf = max(0.25, min(func.confidence, 0.49))
    _print_panel(console, func, 4, conf, warnings)
    return 4, conf, warnings
