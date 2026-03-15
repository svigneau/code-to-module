"""Discover functionalities in a CodeSource via rule-based and LLM detection."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import anthropic as anthropic_module
from rich.console import Console
from rich.panel import Panel

from code_to_module.models import (
    CodeSource,
    DetectionMethod,
    DiscoveryResult,
    FunctionalitySpec,
)

_DEFAULT_MODEL = "claude-sonnet-4-6"
_MAX_CODE_LINES_MULTI = 200
_MAX_CODE_LINES_SINGLE = 400

_CONFIDENCE_SCRIPTS = 0.95
_CONFIDENCE_CLICK = 0.92
_CONFIDENCE_ARGPARSE = 0.88
_CONFIDENCE_SHELL = 0.82
_CONFIDENCE_MULTI = 0.78
_DOC_CONFIDENCE_BONUS = 0.10

_discover_console = Console()

_SKIP_BRANCHES = frozenset(["help", "--help", "-h", "usage", "version", "--version"])

_SYSTEM_PROMPT = """\
You are analysing a bioinformatics codebase to identify distinct, independently-\
invokable functionalities that could each become a separate nf-core module.

A functionality qualifies if it:
1. Takes specific inputs and produces specific outputs
2. Can be invoked independently (not just a helper function)
3. Represents a meaningful bioinformatics operation

You MUST respond with valid JSON only.

Required JSON structure:
{
  "functionalities": [
    {
      "name": "align",
      "display_name": "Align reads to reference",
      "description": "Maps short reads to a reference genome using BWA-MEM",
      "entry_point": "align subcommand or align.py",
      "inferred_inputs": ["reads", "reference"],
      "inferred_outputs": ["alignments"],
      "confidence": 0.85,
      "reasoning": "One sentence explaining why this is a distinct functionality"
    }
  ],
  "is_single_functionality": false,
  "reasoning": "Overall assessment of the codebase structure"
}

Rules:
- Confidence 0.85+: clearly distinct, separate entry point, unambiguous I/O
- Confidence 0.60-0.84: probably distinct but some ambiguity
- Confidence below 0.60: uncertain — user should verify
- If the entire codebase is one operation: return one functionality with is_single_functionality=true
- Do NOT include: helper functions, config loaders, logging utilities, test functions
- Maximum 8 functionalities — if more found, return the 8 most significant
"""


# ── Public API ────────────────────────────────────────────────────────────────


def discover(source: CodeSource) -> DiscoveryResult:
    """Run all detectors and return a DiscoveryResult."""
    rb_specs, rb_method = _run_rule_based(source)
    rule_based_found_any = len(rb_specs) > 0

    if len(rb_specs) >= 2:
        specs = rb_specs
        method: DetectionMethod = rb_method  # type: ignore[assignment]
        is_single = False
    else:
        # Never let the LLM override or expand a structured CLI signal:
        # console_scripts is the package author's explicit declaration;
        # click/argparse/shell detectors parse real code and carry I/O signal.
        # LLM over-splits flat tools into internal sub-operations whose
        # code_section is empty, which assess() rates Tier 5 → no module
        # generated.  Only fall back to LLM when rule-based found nothing.
        _STRUCTURED_METHODS = {
            DetectionMethod.CONSOLE_SCRIPTS,
            DetectionMethod.CLICK_DECORATOR,
            DetectionMethod.ARGPARSE_SUBPARSER,
            DetectionMethod.SHELL_CASE_STATEMENT,
        }
        if rb_method in _STRUCTURED_METHODS:
            llm_specs: list[FunctionalitySpec] = []
        else:
            llm_specs = _run_llm(source)
        if len(llm_specs) >= 2:
            specs = llm_specs
            method = DetectionMethod.LLM_INFERENCE
            is_single = False
        else:
            if rb_specs:
                # Prefer the rule-based single spec — it carries richer I/O signal
                # (click params, argparse args) than a name-only _make_single_spec.
                specs = rb_specs
                method = rb_method  # type: ignore[assignment]
            else:
                confidence = 1.0 if rule_based_found_any else 0.75
                specs = [_make_single_spec(source, confidence)]
                method = DetectionMethod.SINGLE_SCRIPT
            is_single = True

    specs = _apply_existing_module_naming_pass(specs, source)

    # Deduplicate by name, keeping first occurrence (highest confidence after sort)
    seen_names: set[str] = set()
    deduped: list[FunctionalitySpec] = []
    for spec in specs:
        if spec.name in seen_names:
            _discover_console.print(
                f"[yellow]⚠ Dropping duplicate functionality: {spec.name}[/yellow]"
            )
            continue
        seen_names.add(spec.name)
        deduped.append(spec)
    specs = deduped

    specs.sort(key=lambda s: -s.confidence)

    return DiscoveryResult(
        source=source,
        functionalities=specs,
        selected=[],
        detection_method_used=method,
        is_single_functionality=is_single,
    )


def select_functionalities(
    result: DiscoveryResult,
    functionalities_flag: str | None,
    all_flag: bool,
    no_interaction: bool,
    console: Console,
) -> DiscoveryResult:
    """Populate result.selected based on flags and optional interactive UI."""
    specs = result.functionalities

    # Case 1: single functionality — no menu
    if result.is_single_functionality:
        name = specs[0].name if specs else "unknown"
        console.print(f"→ Single functionality: {name}")
        return result.model_copy(update={"selected": list(specs)})

    # Case 2: --all-functionalities
    if all_flag:
        console.print(f"→ Selected all {len(specs)} functionalities")
        return result.model_copy(update={"selected": list(specs)})

    # Case 3: --functionalities name,name
    if functionalities_flag:
        requested = [n.strip() for n in functionalities_flag.split(",") if n.strip()]
        spec_by_name = {s.name: s for s in specs}
        selected: list[FunctionalitySpec] = []
        for name in requested:
            if name in spec_by_name:
                selected.append(spec_by_name[name])
            else:
                console.print(f"[yellow]Warning: unknown functionality '{name}', skipping[/yellow]")
        return result.model_copy(update={"selected": selected})

    # Case 4: non-interactive (flag or non-TTY)
    if no_interaction or not _is_tty():
        selected = [s for s in specs if s.pre_selected]
        for s in selected:
            console.print(f"→ Auto-selected: {s.name} (confidence {s.confidence:.0%})")
        return result.model_copy(update={"selected": selected})

    # Case 5: interactive TTY
    return _interactive_select(result, specs, console)


# ── console_scripts helpers ───────────────────────────────────────────────────


def _parse_setup_py(setup_py: Path) -> list[str]:
    """Return console_scripts entries from setup.py via AST parsing."""
    try:
        text = setup_py.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
    except Exception:
        return []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_setup = (isinstance(func, ast.Name) and func.id == "setup") or (
            isinstance(func, ast.Attribute) and func.attr == "setup"
        )
        if not is_setup:
            continue
        for kw in node.keywords:
            if kw.arg != "entry_points":
                continue
            val = kw.value
            if not isinstance(val, ast.Dict):
                continue
            for key, value in zip(val.keys, val.values):
                if not isinstance(key, ast.Constant) or key.value != "console_scripts":
                    continue
                if isinstance(value, (ast.List, ast.Tuple)):
                    return [
                        elt.value
                        for elt in value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    ]
    return []


def _parse_setup_cfg(setup_cfg: Path) -> list[str]:
    """Return console_scripts entries from setup.cfg."""
    import configparser

    try:
        cfg = configparser.ConfigParser()
        cfg.read_string(setup_cfg.read_text(encoding="utf-8", errors="replace"))
        if not cfg.has_section("options.entry_points"):
            return []
        raw = cfg.get("options.entry_points", "console_scripts", fallback="")
        return [ln.strip() for ln in raw.splitlines() if "=" in ln.strip()]
    except Exception:
        return []


def _parse_pyproject_toml(pyproject: Path) -> list[str]:
    """Return console_scripts entries from pyproject.toml."""
    try:
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                return []
        data = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="replace"))
        scripts = data.get("project", {}).get("scripts", {})
        if not scripts:
            scripts = data.get("tool", {}).get("poetry", {}).get("scripts", {})
        return [f"{k}={v}" for k, v in scripts.items()]
    except Exception:
        return []


def _parse_console_scripts_entry(entry: str) -> tuple[str, str, str] | None:
    """Parse 'name = module:function' → (name, module, function), or None."""
    entry = entry.strip()
    if "=" not in entry or ":" not in entry:
        return None
    name_part, rest = entry.split("=", 1)
    name = name_part.strip()
    if ":" not in rest:
        return None
    module, func = rest.strip().split(":", 1)
    return name.strip(), module.strip(), func.strip()


def _resolve_module_path(module_dotted: str, repo_root: Path) -> Path | None:
    """Resolve 'package.submodule' to its .py file under repo_root."""
    rel = Path(module_dotted.replace(".", "/") + ".py")
    for candidate in (repo_root / "src" / rel, repo_root / rel):
        if candidate.is_file():
            return candidate
    return None


_FILE_IO_RE = re.compile(
    r"\bPath\b|open\s*\(|\.write\s*\(|\.read\s*\(|argparse\.FileType"
    r"|click\.Path|click\.File",
    re.IGNORECASE,
)


def _block_has_file_io(block_code: str) -> bool:
    """Return True if code block contains any file I/O signal."""
    return bool(_FILE_IO_RE.search(block_code))


# ── Level 1: argparse add_subparsers ─────────────────────────────────────────


def _extract_subcommands_from_file(
    code: str, entry_point: str
) -> list[FunctionalitySpec]:
    """Level 1: If code uses argparse add_subparsers, return a FunctionalitySpec
    per add_parser call.  Returns [] when not applicable.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    has_subparsers = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_subparsers"
        for node in ast.walk(tree)
    )
    if not has_subparsers:
        return []

    lines = code.splitlines()
    specs: list[FunctionalitySpec] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "add_parser"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        name = str(node.args[0].value)
        start = node.lineno - 1
        end = getattr(node, "end_lineno", node.lineno)
        code_section = "\n".join(lines[start:end])
        specs.append(
            FunctionalitySpec(
                name=name,
                display_name=name.replace("_", " ").title(),
                description=f"Subcommand: {name}",
                detection_method=DetectionMethod.ARGPARSE_SUBPARSER,
                confidence=_CONFIDENCE_ARGPARSE,
                code_section=code_section,
                entry_point=entry_point,
                pre_selected=True,
            )
        )
    return specs


# ── Level 2: mutually-exclusive groups + early-exit mode flags ────────────────


def _extract_meg_modes(
    tree: ast.AST, code: str, tool_name: str, entry_point: str
) -> list[FunctionalitySpec]:
    """Level 2a: detect add_mutually_exclusive_group() blocks.

    Identifies argparse MEGs and returns one spec per non-utility mode.
    Uses 'tool_name_flag' naming: e.g. mytool_align, mytool_sort.
    """
    lines = code.splitlines()
    specs: list[FunctionalitySpec] = []

    # Collect all add_argument calls that are chained onto a MEG variable.
    # We track: assignment targets that receive add_mutually_exclusive_group()
    meg_vars: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not (isinstance(call.func, ast.Attribute) and call.func.attr == "add_mutually_exclusive_group"):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                meg_vars.add(target.id)

    if not meg_vars:
        return []

    # For each MEG variable, find add_argument calls on it
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument"):
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id in meg_vars):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        flag = str(node.args[0].value).lstrip("-").replace("-", "_")
        start = node.lineno - 1
        end = getattr(node, "end_lineno", node.lineno)
        snippet = "\n".join(lines[start:end])
        if not _block_has_file_io(snippet):
            continue
        mode_name = f"{tool_name}_{flag}"
        specs.append(
            FunctionalitySpec(
                name=mode_name,
                display_name=mode_name.replace("_", " ").title(),
                description=f"Mode: {flag} (mutually exclusive)",
                detection_method=DetectionMethod.CONSOLE_SCRIPTS,
                confidence=_CONFIDENCE_SCRIPTS - 0.05,
                code_section=snippet,
                entry_point=entry_point,
                pre_selected=True,
            )
        )
    return specs


def _extract_early_exit_modes(
    tree: ast.AST, code: str, tool_name: str, entry_point: str
) -> list[FunctionalitySpec]:
    """Level 2b: detect early-exit if blocks in the main function.

    Pattern: `if flag_variable: ... exit(0)` at the top level of the CLI
    entry function.  Only returns modes where the body contains file I/O —
    utility modes (e.g. --update-models, --show-models) are filtered out.
    Modes are named: tool_name_flag (e.g. celltypist_annotate).
    """
    # Find the top-level CLI function (click @command or argparse main)
    main_func: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if dec.func.attr in ("command", "group"):
                    main_func = node
                    break
        if main_func:
            break
    # Fall back: function named "main" or "cli" at module level
    if main_func is None:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in ("main", "cli", "run"):
                main_func = node
                break

    if main_func is None:
        return []

    lines = code.splitlines()
    specs: list[FunctionalitySpec] = []

    for stmt in main_func.body:
        if not isinstance(stmt, ast.If):
            continue
        # Require a simple variable name as condition (e.g. `if update_models:`)
        if not isinstance(stmt.test, ast.Name):
            continue
        flag_name = stmt.test.id
        body = stmt.body
        if not body:
            continue
        # Check the body ends with exit() / sys.exit() / return
        last = body[-1]
        is_early_exit = (
            isinstance(last, ast.Return)
            or (
                isinstance(last, ast.Expr)
                and isinstance(last.value, ast.Call)
                and (
                    (isinstance(last.value.func, ast.Name) and last.value.func.id in ("exit", "quit"))
                    or (
                        isinstance(last.value.func, ast.Attribute)
                        and last.value.func.attr in ("exit",)
                    )
                )
            )
        )
        if not is_early_exit:
            continue
        start = stmt.lineno - 1
        end = getattr(stmt, "end_lineno", stmt.lineno)
        block_code = "\n".join(lines[start:end])
        # Filter: skip utility modes that have no file I/O
        if not _block_has_file_io(block_code):
            continue
        mode_name = f"{tool_name}_{flag_name}"
        specs.append(
            FunctionalitySpec(
                name=mode_name,
                display_name=mode_name.replace("_", " ").title(),
                description=f"Mode: {flag_name}",
                detection_method=DetectionMethod.CONSOLE_SCRIPTS,
                confidence=_CONFIDENCE_SCRIPTS - 0.05,
                code_section=block_code,
                entry_point=entry_point,
                pre_selected=True,
            )
        )
    return specs


def _extract_mode_specs(
    code: str, tool_name: str, entry_point: str
) -> list[FunctionalitySpec]:
    """Level 2 dispatcher: try MEG then early-exit modes.

    Returns the union of specs found by both strategies. The caller falls
    through to Level 3 (single spec) when this returns [].
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    meg_specs = _extract_meg_modes(tree, code, tool_name, entry_point)
    exit_specs = _extract_early_exit_modes(tree, code, tool_name, entry_point)

    # Deduplicate by name (MEG takes precedence if both detect the same flag)
    seen: set[str] = {s.name for s in meg_specs}
    combined = list(meg_specs)
    for s in exit_specs:
        if s.name not in seen:
            combined.append(s)
            seen.add(s.name)
    return combined


def _detect_console_scripts(source: CodeSource) -> list[FunctionalitySpec]:
    """Detector A0: read console_scripts from setup.py / setup.cfg / pyproject.toml.

    This is the most authoritative signal a Python package can provide about
    its CLI entry points — checked before any AST heuristic.

    For each entry:
    - If the target file uses argparse add_subparsers, expand to one
      FunctionalitySpec per subcommand (the only legitimate reason to split).
    - Otherwise, return a single FunctionalitySpec with the full file as
      code_section so assess.py receives real code.
    """
    repo_root = source.repo_root
    if repo_root is None:
        return []

    entries: list[str] = []
    for parser, path in [
        (_parse_setup_py, repo_root / "setup.py"),
        (_parse_setup_cfg, repo_root / "setup.cfg"),
        (_parse_pyproject_toml, repo_root / "pyproject.toml"),
    ]:
        if path.is_file():
            entries = parser(path)
            if entries:
                break

    if not entries:
        return []

    specs: list[FunctionalitySpec] = []
    for entry in entries:
        parsed = _parse_console_scripts_entry(entry)
        if parsed is None:
            continue
        cmd_name, module, func_name = parsed

        src_file = _resolve_module_path(module, repo_root)
        if src_file is not None:
            try:
                code_section = src_file.read_text(encoding="utf-8", errors="replace")
                entry_point = str(src_file.relative_to(repo_root))
            except Exception:
                code_section = f"# {module}:{func_name}"
                entry_point = module.replace(".", "/") + ".py"
        else:
            code_section = f"# {module}:{func_name}"
            entry_point = module.replace(".", "/") + ".py"

        # Three-level cascade — stop at the first level that produces specs.
        #
        # Level 1: explicit argparse subparsers (add_subparsers / add_parser)
        #   One spec per named subcommand.
        #
        # Level 2: mutually-exclusive groups or mode-switching flags
        #   One spec per mode (with file I/O); utility modes filtered out.
        #   Spec names: cmd_name + "_" + mode_flag.
        #
        # Level 3: flat CLI — one spec for the whole entry point.
        #   Name = console_scripts key (cmd_name).
        subcmd_specs = _extract_subcommands_from_file(code_section, entry_point)
        if subcmd_specs:
            # Level 1
            specs.extend(subcmd_specs)
        else:
            mode_specs = _extract_mode_specs(code_section, cmd_name, entry_point)
            if mode_specs:
                # Level 2
                specs.extend(mode_specs)
            else:
                # Level 3
                specs.append(
                    FunctionalitySpec(
                        name=cmd_name,
                        display_name=cmd_name.replace("-", " ").replace("_", " ").title(),
                        description=f"CLI entry point: {cmd_name} = {module}:{func_name}",
                        detection_method=DetectionMethod.CONSOLE_SCRIPTS,
                        confidence=_CONFIDENCE_SCRIPTS,
                        code_section=code_section,
                        entry_point=entry_point,
                        pre_selected=True,
                    )
                )
    return specs


# ── Rule-based detectors ──────────────────────────────────────────────────────


def _run_rule_based(
    source: CodeSource,
) -> tuple[list[FunctionalitySpec], DetectionMethod | None]:
    """Try each rule-based detector in priority order; stop at first ≥2 result.

    NOTE: source.doc_sources are NOT consulted here — rule-based detection
    operates solely on source.repo_manifest (parsed AST / regex patterns).
    TODO: a future improvement would pre-scan doc_sources for documented
    function signatures (e.g. API reference pages) and inject synthetic
    RepoFile entries into the manifest before rule-based detection runs,
    giving detectors richer signal for packages whose entry points are only
    described in documentation (e.g. pure-library packages with no CLI).
    """
    # console_scripts is authoritative: return immediately if any entries found
    cs_specs = _detect_console_scripts(source)
    if cs_specs:
        return cs_specs, DetectionMethod.CONSOLE_SCRIPTS

    first_single: tuple[list[FunctionalitySpec], DetectionMethod] | None = None

    for detect_fn, method in [
        (_detect_click, DetectionMethod.CLICK_DECORATOR),
        (_detect_argparse, DetectionMethod.ARGPARSE_SUBPARSER),
        (_detect_shell_case, DetectionMethod.SHELL_CASE_STATEMENT),
    ]:
        specs = detect_fn(source)
        if len(specs) >= 2:
            return specs, method
        if specs and first_single is None:
            first_single = (specs, method)

    # Multi-script detection is a last resort — only run when no structured CLI
    # detector (click/argparse/shell) found anything.  Skipping it when
    # first_single is set prevents package-internal modules (e.g. MultiQC's
    # helper scripts) from drowning out the true Click/argparse entry point.
    if first_single is None:
        multi_specs = _detect_multi_script(source)
        if len(multi_specs) >= 2:
            return multi_specs, DetectionMethod.MULTI_SCRIPT_REPO
        if multi_specs:
            first_single = (multi_specs, DetectionMethod.MULTI_SCRIPT_REPO)

    if first_single:
        return first_single
    return [], None


def _detect_click(source: CodeSource) -> list[FunctionalitySpec]:
    """Detector A: find functions decorated with @{group}.command()."""
    specs: list[FunctionalitySpec] = []
    for repo_file in source.repo_manifest:
        if repo_file.language != "python":
            continue
        try:
            text = _read_repo_file(source, repo_file)
            tree = ast.parse(text)
        except Exception:
            continue

        lines = text.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not any(
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "command"
                for dec in node.decorator_list
            ):
                continue

            name = node.name
            description = ast.get_docstring(node) or f"Command: {name}"
            # Include decorator lines so assess/infer see @click.option definitions
            deco_start = (
                node.decorator_list[0].lineno - 1
                if node.decorator_list
                else node.lineno - 1
            )
            code_section = "\n".join(lines[deco_start : node.end_lineno])
            inputs, outputs = _extract_click_params(node)

            specs.append(
                FunctionalitySpec(
                    name=name,
                    display_name=name.replace("_", " ").title(),
                    description=description,
                    detection_method=DetectionMethod.CLICK_DECORATOR,
                    confidence=_CONFIDENCE_CLICK,
                    code_section=code_section,
                    entry_point=name,
                    inferred_inputs=inputs,
                    inferred_outputs=outputs,
                    pre_selected=True,
                )
            )
    return specs


def _extract_click_params(node: ast.FunctionDef) -> tuple[list[str], list[str]]:
    inputs: list[str] = []
    outputs: list[str] = []
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        if not isinstance(dec.func, ast.Attribute):
            continue
        attr = dec.func.attr
        str_args = [
            str(a.value)
            for a in dec.args
            if isinstance(a, ast.Constant) and isinstance(a.value, str)
        ]
        if attr == "argument":
            if str_args:
                inputs.append(str_args[0].lower().replace("-", "_"))
        elif attr == "option":
            is_output = any(
                n in ("-o",) or n in ("--output", "--out") or n.endswith("-output") or n.endswith("-out")
                for n in str_args
            )
            long_name = next(
                (n.lstrip("-").replace("-", "_") for n in str_args if n.startswith("--")),
                None,
            )
            if is_output and long_name:
                outputs.append(long_name)
            elif long_name:
                inputs.append(long_name)
    return inputs, outputs


def _detect_argparse(source: CodeSource) -> list[FunctionalitySpec]:
    """Detector B: find argparse subcommands via add_subparsers + add_parser."""
    specs: list[FunctionalitySpec] = []
    for repo_file in source.repo_manifest:
        if repo_file.language != "python":
            continue
        try:
            text = _read_repo_file(source, repo_file)
            tree = ast.parse(text)
        except Exception:
            continue

        has_subparsers = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_subparsers"
            for node in ast.walk(tree)
        )
        if not has_subparsers:
            continue

        lines = text.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Attribute) and node.func.attr == "add_parser"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            name = str(node.args[0].value)
            start = node.lineno - 1
            end = getattr(node, "end_lineno", node.lineno)
            code_section = "\n".join(lines[start:end])
            specs.append(
                FunctionalitySpec(
                    name=name,
                    display_name=name.replace("_", " ").title(),
                    description=f"Subcommand: {name}",
                    detection_method=DetectionMethod.ARGPARSE_SUBPARSER,
                    confidence=_CONFIDENCE_ARGPARSE,
                    code_section=code_section,
                    entry_point=name,
                    pre_selected=True,
                )
            )
    return specs


def _detect_shell_case(source: CodeSource) -> list[FunctionalitySpec]:
    """Detector C: find branches in bash case statements."""
    specs: list[FunctionalitySpec] = []
    branch_re = re.compile(r"^\s{2,}(\w[\w-]*)\s*\)\s*$")

    for repo_file in source.repo_manifest:
        if repo_file.language != "bash":
            continue
        try:
            text = _read_repo_file(source, repo_file)
        except Exception:
            continue

        lines = text.splitlines()

        case_start: int | None = None
        for i, line in enumerate(lines):
            if re.match(r"\s*case\s+", line):
                case_start = i
                break
        if case_start is None:
            continue

        case_end = len(lines)
        for i in range(case_start + 1, len(lines)):
            if re.match(r"\s*esac\s*$", lines[i]):
                case_end = i
                break

        i = case_start + 1
        while i < case_end:
            m = branch_re.match(lines[i])
            if m:
                name = m.group(1)
                if name not in _SKIP_BRANCHES:
                    body_lines: list[str] = []
                    j = i + 1
                    while j < case_end:
                        if re.match(r"\s*;;\s*$", lines[j]):
                            break
                        body_lines.append(lines[j])
                        j += 1
                    code_section = lines[i] + "\n" + "\n".join(body_lines)
                    tool_hints = [
                        tc for bl in body_lines for tc in re.findall(r"\b(\w+)\s+\$@", bl)
                    ]
                    specs.append(
                        FunctionalitySpec(
                            name=name,
                            display_name=name.replace("_", " ").title(),
                            description=f"Shell command: {name}",
                            detection_method=DetectionMethod.SHELL_CASE_STATEMENT,
                            confidence=_CONFIDENCE_SHELL,
                            code_section=code_section,
                            entry_point=name,
                            inferred_inputs=tool_hints,
                            pre_selected=True,
                        )
                    )
                    i = j + 1
                    continue
            i += 1

    return specs


def _is_package_submodule(repo_file: object, source: CodeSource) -> bool:
    """Return True if repo_file lives inside a Python package (parent has __init__.py)."""
    from code_to_module.models import RepoFile as _RF

    rf: _RF = repo_file  # type: ignore[assignment]
    if source.repo_root is None:
        return False
    parent = (source.repo_root / rf.path).parent
    return (parent / "__init__.py").is_file()


def _detect_multi_script(source: CodeSource) -> list[FunctionalitySpec]:
    """Detector D: multiple independent entrypoint script files.

    Excludes files that are submodules of a Python package (their parent
    directory contains __init__.py), as those are library internals, not
    independently invokable scripts.
    """
    entrypoints = [
        f for f in source.repo_manifest
        if f.is_likely_entrypoint and not _is_package_submodule(f, source)
    ]
    if len(entrypoints) < 2:
        return []

    specs: list[FunctionalitySpec] = []
    for repo_file in entrypoints:
        try:
            text = _read_repo_file(source, repo_file)
        except Exception:
            continue
        name = repo_file.path.stem
        description = _extract_docstring_or_comment(text) or f"Script: {repo_file.path.name}"
        inputs, outputs = _extract_script_io(text, repo_file.language)
        specs.append(
            FunctionalitySpec(
                name=name,
                display_name=name.replace("_", " ").title(),
                description=description,
                detection_method=DetectionMethod.MULTI_SCRIPT_REPO,
                confidence=_CONFIDENCE_MULTI,
                code_section=text,
                entry_point=str(repo_file.path),
                inferred_inputs=inputs,
                inferred_outputs=outputs,
                pre_selected=True,
            )
        )
    return specs


# ── LLM fallback ──────────────────────────────────────────────────────────────


def _run_llm(source: CodeSource) -> list[FunctionalitySpec]:
    """Call the Anthropic API to identify functionalities."""
    try:
        system, user = _build_llm_prompt(source)
        client = anthropic_module.Anthropic()
        response = client.messages.create(
            model=_DEFAULT_MODEL,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = response.content[0].text  # type: ignore[union-attr]
        specs = _parse_llm_response(text)
        if source.doc_sources:
            specs = _apply_doc_bonus(specs, source)
        return specs
    except Exception:
        return []


_CLI_NAMES = frozenset(["cli", "command_line", "__main__", "main", "run", "app"])


def _build_llm_prompt(source: CodeSource) -> tuple[str, str]:
    # Prefer CLI-named files so the LLM sees the actual entry point first
    all_eps = [f for f in source.repo_manifest if f.is_likely_entrypoint]
    cli_eps = [f for f in all_eps if f.path.stem.lower() in _CLI_NAMES]
    other_eps = [f for f in all_eps if f.path.stem.lower() not in _CLI_NAMES]
    # If no marked entrypoints have CLI names, also check all Python files
    if not cli_eps:
        cli_eps = [
            f for f in source.repo_manifest
            if f.language == "python" and f.path.stem.lower() in _CLI_NAMES
        ]
    entrypoints = (cli_eps + other_eps)[:3]

    if not entrypoints:
        code_parts = ["\n".join(source.raw_code.splitlines()[:_MAX_CODE_LINES_SINGLE])]
    elif len(entrypoints) == 1:
        text = _read_repo_file(source, entrypoints[0])
        code_parts = [
            f"# {entrypoints[0].path}\n"
            + "\n".join(text.splitlines()[:_MAX_CODE_LINES_SINGLE])
        ]
    else:
        code_parts = []
        for ep in entrypoints:
            text = _read_repo_file(source, ep)
            code_parts.append(
                f"# {ep.path}\n" + "\n".join(text.splitlines()[:_MAX_CODE_LINES_MULTI])
            )

    repo_name = source.url or (str(source.path) if source.path else source.filename)
    languages = ", ".join(
        sorted({f.language for f in source.repo_manifest if f.language != "other"})
    ) or source.language
    filenames = ", ".join(str(f.path) for f in entrypoints) or source.filename

    user = (
        f"Repository: {repo_name}\n"
        f"Language(s): {languages}\n"
        f"Files analysed: {filenames}\n\n"
        f"Code:\n" + "\n\n".join(code_parts)
    )

    doc_section = _build_doc_section(source)
    if doc_section:
        user += f"\n\n{doc_section}"

    user += "\n\nIdentify distinct functionalities that could each become an nf-core module."
    return _SYSTEM_PROMPT, user


def _build_doc_section(source: CodeSource) -> str:
    valid_docs = [d for d in source.doc_sources if not d.fetch_error and d.content]
    if not valid_docs:
        return ""
    section = "Documentation and tutorials:\n"
    for doc in valid_docs:
        label = doc.url or str(doc.path)
        section += f"--- {label} ---\n{doc.content}\n"
    section += (
        "\nUse the documentation above to identify which functions/methods are the "
        "primary API surface that users actually call, rather than internal helpers. "
        "Documentation-confirmed entry points should receive a confidence bonus of +0.10 "
        "(capped at 1.0) compared to code-only inference."
    )
    return section


def _apply_doc_bonus(
    specs: list[FunctionalitySpec], source: CodeSource
) -> list[FunctionalitySpec]:
    """Apply +0.10 confidence bonus if name/entry_point appears in doc content."""
    all_doc_content = " ".join(
        d.content for d in source.doc_sources if not d.fetch_error
    ).lower()
    if not all_doc_content.strip():
        return specs
    result: list[FunctionalitySpec] = []
    for spec in specs:
        terms = [spec.name.lower()]
        if spec.entry_point:
            terms.append(spec.entry_point.lower())
        if any(term in all_doc_content for term in terms):
            spec = spec.model_copy(
                update={"confidence": min(1.0, spec.confidence + _DOC_CONFIDENCE_BONUS)}
            )
        result.append(spec)
    return result


def _parse_llm_response(text: str) -> list[FunctionalitySpec]:
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if not json_match:
        return []
    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError:
        return []
    specs: list[FunctionalitySpec] = []
    for func in (data.get("functionalities") or [])[:8]:
        name = func.get("name", "")
        if not name:
            continue
        confidence = float(func.get("confidence", 0.75))
        specs.append(
            FunctionalitySpec(
                name=name,
                display_name=func.get("display_name", name.replace("_", " ").title()),
                description=func.get("description", ""),
                detection_method=DetectionMethod.LLM_INFERENCE,
                confidence=confidence,
                code_section="",
                entry_point=func.get("entry_point"),
                inferred_inputs=func.get("inferred_inputs", []),
                inferred_outputs=func.get("inferred_outputs", []),
                pre_selected=confidence >= 0.70,
            )
        )
    return specs


# ── Single-script fallback ────────────────────────────────────────────────────


def _make_single_spec(source: CodeSource, confidence: float = 0.75) -> FunctionalitySpec:
    name = Path(source.filename).stem if source.filename else "main"
    return FunctionalitySpec(
        name=name,
        display_name=name.replace("_", " ").title(),
        description="Single-functionality tool",
        detection_method=DetectionMethod.SINGLE_SCRIPT,
        confidence=confidence,
        code_section=source.raw_code,
        pre_selected=True,
    )


# ── Naming convention pass ────────────────────────────────────────────────────


def _apply_existing_module_naming_pass(
    specs: list[FunctionalitySpec],
    source: CodeSource,
) -> list[FunctionalitySpec]:
    if not source.existing_modules:
        return specs
    existing_subcommands = {
        em.subcommand.lower() for em in source.existing_modules if em.subcommand
    }
    result: list[FunctionalitySpec] = []
    for spec in specs:
        if spec.name.lower() in existing_subcommands:
            spec = spec.model_copy(
                update={
                    "pre_selected": True,
                    "confidence": min(1.0, spec.confidence + 0.05),
                }
            )
        result.append(spec)
    return result


# ── Interactive selection ─────────────────────────────────────────────────────


def _interactive_select(
    result: DiscoveryResult,
    specs: list[FunctionalitySpec],
    console: Console,
) -> DiscoveryResult:
    source = result.source
    title = source.url or (str(source.path) if source.path else source.filename)
    lines: list[str] = [f"  Functionalities found in: {title}\n"]
    for i, spec in enumerate(specs, 1):
        if spec.confidence < 0.50:
            marker = "⚠"
        elif spec.pre_selected:
            marker = "✓"
        else:
            marker = " "
        lines.append(f"  [{i}] {marker} {spec.name:<16} {spec.description}")
        if spec.inferred_inputs:
            lines.append(f"       Inputs:  {', '.join(spec.inferred_inputs)}")
        if spec.inferred_outputs:
            lines.append(f"       Outputs: {', '.join(spec.inferred_outputs)}")
        lines.append(
            f"       Confidence: {spec.confidence:.0%}  "
            f"Detection: {_format_detection_method(spec.detection_method)}"
        )
        lines.append("")

    console.print(Panel("\n".join(lines), border_style="blue"))

    default_indices = [i for i, s in enumerate(specs, 1) if s.pre_selected]
    default_str = ",".join(str(i) for i in default_indices)

    raw = input(
        f"Select [{default_str}] (Enter to accept, or type new selection, 'all', 'q' to quit): "
    ).strip()
    if not raw:
        raw = default_str

    if raw == "all":
        selected = list(specs)
    elif raw == "q":
        selected = []
    else:
        try:
            indices = _parse_selection(raw)
            selected = [specs[i - 1] for i in indices if 1 <= i <= len(specs)]
        except ValueError:
            console.print(f"[red]Invalid selection '{raw}', using defaults[/red]")
            selected = [s for s in specs if s.pre_selected]

    names = ", ".join(s.name for s in selected)
    console.print(f"→ Will generate {len(selected)} module(s): {names}")
    return result.model_copy(update={"selected": selected})


def _parse_selection(input_str: str) -> list[int]:
    indices: list[int] = []
    for part in input_str.split(","):
        part = part.strip()
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            indices.extend(range(int(start_s), int(end_s) + 1))
        else:
            indices.append(int(part))
    return indices


def _format_detection_method(method: DetectionMethod) -> str:
    return {
        DetectionMethod.CONSOLE_SCRIPTS: "console_scripts entry point",
        DetectionMethod.CLICK_DECORATOR: "CLI subcommand (@command)",
        DetectionMethod.ARGPARSE_SUBPARSER: "CLI subcommand (argparse)",
        DetectionMethod.SHELL_CASE_STATEMENT: "shell case dispatch",
        DetectionMethod.MULTI_SCRIPT_REPO: "multiple script files",
        DetectionMethod.LLM_INFERENCE: "LLM inference",
        DetectionMethod.SINGLE_SCRIPT: "single script",
    }.get(method, method.value)


def _is_tty() -> bool:
    return sys.stdin.isatty()


def _read_repo_file(source: CodeSource, repo_file: object) -> str:
    from code_to_module.models import RepoFile as _RF

    rf: _RF = repo_file  # type: ignore[assignment]
    if source.repo_root is not None:
        return (source.repo_root / rf.path).read_text(encoding="utf-8", errors="replace")
    return rf.path.read_text(encoding="utf-8", errors="replace")


def _extract_docstring_or_comment(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            return stripped.lstrip("#").strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            content = stripped.strip("\"'").strip()
            if content:
                return content
    try:
        tree = ast.parse(text)
        doc = ast.get_docstring(tree)
        if doc:
            return doc.split("\n")[0]
    except Exception:
        pass
    return ""


def _extract_script_io(text: str, language: str) -> tuple[list[str], list[str]]:
    inputs: list[str] = []
    outputs: list[str] = []
    if language != "python":
        return inputs, outputs
    try:
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
            ):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            arg_name = str(node.args[0].value)
            if arg_name.startswith("--"):
                clean = arg_name.lstrip("-").replace("-", "_")
                if any(kw in clean.lower() for kw in ["output", "outdir", "out"]):
                    outputs.append(clean)
                else:
                    inputs.append(clean)
            elif not arg_name.startswith("-"):
                inputs.append(arg_name)
    except Exception:
        pass
    return inputs, outputs
