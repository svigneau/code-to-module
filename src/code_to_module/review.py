"""Static analysis of nf-core module style conventions.

review_module() reads module files and applies rule-based checks.
It never runs subprocesses — no nf-core lint, no nf-test.
Output: ReviewReport with ReviewItems grouped by severity.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel

from code_to_module.standards.loader import Standards

# ── Models ─────────────────────────────────────────────────────────────────────

Severity = Literal["ERROR", "WARNING", "INFO"]

_ICON: dict[str, str] = {
    "ERROR": "✗",
    "WARNING": "⚠",
    "INFO": "ℹ",
}


class ReviewItem(BaseModel):
    severity: Severity
    category: str
    message: str


class ReviewReport(BaseModel):
    module_path: str = ""
    items: list[ReviewItem] = []
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    submission_ready: bool = True


# ── Parsing helpers ────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    """Return file text or empty string if missing."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _load_yaml(path: Path) -> dict[str, Any] | None:
    """Parse a YAML file with ruamel. Returns None on failure."""
    try:
        from ruamel.yaml import YAML

        yaml = YAML()
        data = yaml.load(_read(path))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _script_block(main_nf: str) -> str:
    """Extract the script: ... \"\"\" block from main.nf text."""
    m = re.search(r'script:\s*\n[ \t]*"""(.*?)"""', main_nf, re.DOTALL)
    return m.group(1) if m else ""


def _stub_block(main_nf: str) -> str:
    """Extract the stub: ... \"\"\" block from main.nf text."""
    m = re.search(r'stub:\s*\n[ \t]*"""(.*?)"""', main_nf, re.DOTALL)
    return m.group(1) if m else ""


def _output_emit_names(main_nf: str) -> list[str]:
    """Return list of emit names from the output: block (excludes versions)."""
    output_block_m = re.search(r"\boutput:\s*\n(.*?)\n\s*(?:when:|script:|stub:|\Z)", main_nf, re.DOTALL)
    if not output_block_m:
        return []
    block = output_block_m.group(1)
    names = re.findall(r"emit:\s*(\w+)", block)
    return names


def _input_names(main_nf: str) -> list[str]:
    """Return list of channel name tokens from the input: block."""
    input_block_m = re.search(r"\binput:\s*\n(.*?)\n\s*(?:output:|when:|script:)", main_nf, re.DOTALL)
    if not input_block_m:
        return []
    block = input_block_m.group(1)
    # Collect val/path/file parameter names inside tuple val(x) path(y) etc.
    return re.findall(r"(?:val|path|file)\s*\(\s*(\w+)\s*\)", block)


def _process_label(main_nf: str) -> str:
    m = re.search(r"label\s+'([^']+)'", main_nf)
    return m.group(1) if m else ""


def _tool_from_container(main_nf: str) -> str:
    """Best-effort: extract tool name from the container or conda line."""
    # quay.io/biocontainers/TOOL:... or singularity depot ...TOOL:...
    m = re.search(r"(?:quay\.io/biocontainers/|singularity/|depot\.galaxyproject\.org/singularity/)([a-zA-Z0-9_-]+)[:/]", main_nf)
    if m:
        return m.group(1).lower()
    # conda line: moduleDir/environment.yml fallback — try process name
    proc_m = re.search(r"process\s+([A-Z][A-Z0-9_]*)\s*\{", main_nf)
    if proc_m:
        # TOOLNAME_SUBCOMMAND → first part
        return proc_m.group(1).split("_")[0].lower()
    return ""


def _meta_yml_channel_names(meta_yml: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (input_names, output_names) from meta.yml (excluding 'meta' and 'versions')."""
    def _names(section: str) -> list[str]:
        entries = meta_yml.get(section) or []
        names: list[str] = []
        for entry in entries:
            if isinstance(entry, dict):
                names.extend(k for k in entry.keys() if k not in ("meta", "versions"))
        return names

    return _names("input"), _names("output")


# ── Check categories ───────────────────────────────────────────────────────────

_GENERIC_OUTPUT_NAMES = frozenset({"output", "result", "out", "file", "data", "files", "results"})
_SHORT_STANDARD_NAMES = frozenset({
    "bam", "cram", "sam", "vcf", "bcf", "bed", "gtf", "gff",
    "fasta", "fastq", "fai", "bai", "tbi", "log", "html", "pdf",
    "tsv", "csv", "json", "yaml", "txt", "zip", "tar", "versions",
})
_HEAVY_TOOLS = frozenset({"gatk", "star", "hisat2", "cellranger", "bismark"})
_LIGHT_TOOLS = frozenset({"samtools", "bedtools", "bcftools"})


def _check_channel_naming(main_nf: str) -> list[ReviewItem]:
    items: list[ReviewItem] = []

    out_names = _output_emit_names(main_nf)
    for name in out_names:
        if name in _GENERIC_OUTPUT_NAMES:
            items.append(ReviewItem(
                severity="ERROR",
                category="channel_naming",
                message=(
                    f"Output channel '{name}' is too generic — use a format-specific name"
                    " like 'bam', 'vcf', 'report'"
                ),
            ))
        elif (
            len(name) <= 2
            and name not in _SHORT_STANDARD_NAMES
            and name != "versions"
        ):
            items.append(ReviewItem(
                severity="INFO",
                category="channel_naming",
                message=f"Consider a more descriptive channel name for '{name}'",
            ))

    in_names = _input_names(main_nf)
    if in_names and in_names[0] != "meta":
        items.append(ReviewItem(
            severity="WARNING",
            category="channel_naming",
            message=f"First input channel should be named 'meta' — found '{in_names[0]}'",
        ))

    return items


def _check_process_label(main_nf: str, standards: Standards) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    label = _process_label(main_nf)
    tool = _tool_from_container(main_nf)

    if not label:
        return items

    def _matches(tool_name: str, tool_set: frozenset[str]) -> bool:
        """True if tool_name matches any entry in tool_set (prefix-aware for versioned names)."""
        return any(tool_name == t or tool_name.startswith(t) for t in tool_set)

    if tool:
        if label == "process_single" and _matches(tool, _HEAVY_TOOLS):
            items.append(ReviewItem(
                severity="ERROR",
                category="process_label",
                message=f"'{tool}' typically requires more resources than process_single",
            ))
        elif label == "process_high_memory" and _matches(tool, _LIGHT_TOOLS):
            items.append(ReviewItem(
                severity="WARNING",
                category="process_label",
                message=(
                    f"'{tool}' is typically lightweight — consider process_single or process_medium"
                ),
            ))
        elif (
            tool not in standards.known_tools
            and not _matches(tool, _HEAVY_TOOLS)
            and not _matches(tool, _LIGHT_TOOLS)
        ):
            items.append(ReviewItem(
                severity="INFO",
                category="process_label",
                message=f"Could not verify label appropriateness for '{tool}' — review manually",
            ))
    else:
        items.append(ReviewItem(
            severity="INFO",
            category="process_label",
            message="Could not verify label appropriateness for '(unknown)' — review manually",
        ))

    return items


def _check_ext_args(main_nf: str) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    script = _script_block(main_nf)

    if "task.ext.args" not in main_nf:
        items.append(ReviewItem(
            severity="ERROR",
            category="ext_args",
            message="Module must use 'task.ext.args' — hardcoded parameters are not accepted",
        ))

    # Check for task.ext.prefix absence when named outputs exist
    out_names = _output_emit_names(main_nf)
    non_versions = [n for n in out_names if n != "versions"]
    if non_versions and "task.ext.prefix" not in main_nf:
        items.append(ReviewItem(
            severity="WARNING",
            category="ext_args",
            message="Consider using 'task.ext.prefix ?: meta.id' for output file naming",
        ))

    # Detect hardcoded flags alongside ext.args in the script block
    if script and "task.ext.args" in main_nf:
        # Strip Nextflow ${...} interpolations before scanning for flags so we
        # don't flag --option tokens that are part of variable expressions.
        cleaned_script = re.sub(r"\$\{[^}]*\}", "", script)
        flags = re.findall(r"--[a-zA-Z][-a-zA-Z0-9_]+", cleaned_script)
        for flag in flags:
            items.append(ReviewItem(
                severity="INFO",
                category="ext_args",
                message=f"Hardcoded flag '{flag}' should be movable to ext.args",
            ))

    return items


def _check_meta_yml(
    meta_yml_path: Path, main_nf: str, standards: Standards
) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    data = _load_yaml(meta_yml_path)

    if data is None:
        items.append(ReviewItem(
            severity="ERROR",
            category="meta_yml",
            message="meta.yml is missing or unparseable",
        ))
        return items

    # Required top-level fields
    for field in standards.meta_yml_required_fields:
        if field not in data:
            items.append(ReviewItem(
                severity="ERROR",
                category="meta_yml",
                message=f"Missing required field '{field}' in meta.yml",
            ))

    # Tools: homepage + documentation URLs
    for tool_entry in data.get("tools") or []:
        if isinstance(tool_entry, dict):
            for tool_name, tool_data in tool_entry.items():
                if not isinstance(tool_data, dict):
                    continue
                if not tool_data.get("homepage"):
                    items.append(ReviewItem(
                        severity="ERROR",
                        category="meta_yml",
                        message=f"Tool entry '{tool_name}' missing 'homepage' URL",
                    ))
                if not tool_data.get("documentation"):
                    items.append(ReviewItem(
                        severity="ERROR",
                        category="meta_yml",
                        message=f"Tool entry '{tool_name}' missing 'documentation' URL",
                    ))
                # Placeholder description check
                desc = tool_data.get("description", "")
                if desc and desc.strip().lower() == tool_name.strip().lower():
                    items.append(ReviewItem(
                        severity="WARNING",
                        category="meta_yml",
                        message="Tool description appears to be a placeholder — add a real description",
                    ))

    # Channel cross-reference: meta.yml ↔ main.nf emit names
    main_nf_emits = set(_output_emit_names(main_nf)) - {"versions"}
    _, meta_out_names = _meta_yml_channel_names(data)
    meta_out_set = set(meta_out_names)

    for emit in main_nf_emits:
        if emit not in meta_out_set:
            items.append(ReviewItem(
                severity="ERROR",
                category="meta_yml",
                message=f"Output channel '{emit}' in main.nf not documented in meta.yml",
            ))
    for meta_name in meta_out_set:
        if meta_name not in main_nf_emits:
            items.append(ReviewItem(
                severity="ERROR",
                category="meta_yml",
                message=f"Channel '{meta_name}' in meta.yml has no corresponding emit in main.nf",
            ))

    # EDAM ontology check
    edam_map = standards.edam_for
    for section in ("input", "output"):
        for entry in data.get(section) or []:
            if not isinstance(entry, dict):
                continue
            for ch_name, ch_data in entry.items():
                if ch_name in ("meta", "versions") or not isinstance(ch_data, dict):
                    continue
                if ch_data.get("ontology"):
                    continue  # already has ontology
                pattern = ch_data.get("pattern", "")
                # Derive format from pattern extension
                ext_m = re.search(r"\.([a-zA-Z0-9]+)(?:\}|\s*$|\")", pattern)
                fmt = ext_m.group(1).lower() if ext_m else ""
                if fmt in edam_map:
                    items.append(ReviewItem(
                        severity="WARNING",
                        category="meta_yml",
                        message=(
                            f"Channel '{ch_name}' has format {fmt} — "
                            f"add ontology: [{edam_map[fmt]}]"
                        ),
                    ))

    # Keywords
    keywords = data.get("keywords") or []
    if len(keywords) < 3:
        items.append(ReviewItem(
            severity="INFO",
            category="meta_yml",
            message="Add more keywords to improve discoverability",
        ))

    return items


def _check_versions(main_nf: str) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    script = _script_block(main_nf)
    stub = _stub_block(main_nf)
    combined = script + stub

    if not combined:
        return items

    if "versions.yml" not in combined:
        items.append(ReviewItem(
            severity="ERROR",
            category="versions",
            message="Module must capture software version in versions.yml",
        ))
        return items

    # Fragile version capture: --version without 2>&1 or || true
    version_lines = [
        ln for ln in combined.splitlines()
        if "--version" in ln or "version" in ln.lower()
    ]
    for ln in version_lines:
        stripped = ln.strip()
        if "--version" in stripped and "2>&1" not in stripped and "|| true" not in stripped:
            items.append(ReviewItem(
                severity="WARNING",
                category="versions",
                message="Version capture may fail if tool exits non-zero — add '|| true'",
            ))
            break

    # Non-standard format: check if versions.yml is written via cat <<-END_VERSIONS (standard)
    # or some other approach
    if "versions.yml" in combined and "END_VERSIONS" not in combined:
        # Also acceptable: echo ... >> versions.yml
        if ">>" not in combined and ">" not in combined:
            items.append(ReviewItem(
                severity="INFO",
                category="versions",
                message="Non-standard version capture pattern — check nf-core module template",
            ))

    return items


def _check_conda(env_yml_path: Path, standards: Standards) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    data = _load_yaml(env_yml_path)
    if data is None:
        return items  # environment.yml absent — not an error here

    # Channel order
    channels = data.get("channels") or []
    expected = standards.conda_channels
    # Only check if both lists are non-empty and have same contents but wrong order
    if channels and set(str(c) for c in channels) == set(expected):
        if [str(c) for c in channels] != expected:
            items.append(ReviewItem(
                severity="ERROR",
                category="conda",
                message=f"Conda channels must be ordered: {', '.join(expected)}",
            ))
    elif channels and channels != expected:
        # Different channels entirely — still flag if order is wrong among known ones
        ordered = [c for c in expected if c in channels]
        present = [c for c in channels if c in expected]
        if ordered != present:
            items.append(ReviewItem(
                severity="ERROR",
                category="conda",
                message=f"Conda channels must be ordered: {', '.join(expected)}",
            ))

    # Unpinned dependencies
    for dep in data.get("dependencies") or []:
        dep_str = str(dep).strip()
        if dep_str.startswith("-"):
            dep_str = dep_str[1:].strip()
        # Skip pip sub-lists
        if dep_str == "pip" or ":" in dep_str:
            continue
        pkg_name = re.split(r"[=<>!]", dep_str)[0].strip()
        if pkg_name and "=" not in dep_str and "<" not in dep_str and ">" not in dep_str:
            items.append(ReviewItem(
                severity="WARNING",
                category="conda",
                message=f"Pin dependency versions in environment.yml: '{pkg_name}' has no version",
            ))

    # Environment name convention
    env_name = str(data.get("name", "")).lower()
    if env_name:
        # Extract tool name from the name (e.g. "nf-core-samtools-1.17" → "samtools")
        # We just check it contains something reasonable; compare against process name
        # derived from dependencies
        for dep in data.get("dependencies") or []:
            dep_str = str(dep).strip().lstrip("-").strip()
            if dep_str and "=" in dep_str:
                tool = dep_str.split("=")[0].strip().lower()
                if tool and tool not in env_name:
                    items.append(ReviewItem(
                        severity="INFO",
                        category="conda",
                        message=(
                            f"Convention: environment name should match tool name"
                            f" — found '{env_name}'"
                        ),
                    ))
                break  # only check first real dep

    return items


# ── Aggregation and display ────────────────────────────────────────────────────


def _print_report(
    report: ReviewReport,
    console: Console,
    errors_only: bool = False,
) -> None:
    module_name = Path(report.module_path).name if report.module_path else "module"

    # Summary panel
    ready_str = "[green]YES[/green]" if report.submission_ready else "[red]NO[/red]"
    summary_lines = [
        f"[red]✗  {report.error_count} error{'s' if report.error_count != 1 else ''} (must fix before submission)[/red]"
        if report.error_count else "[green]✗  0 errors[/green]",
        f"[yellow]⚠  {report.warning_count} warning{'s' if report.warning_count != 1 else ''} (should fix)[/yellow]"
        if not errors_only else "",
        f"[blue]ℹ  {report.info_count} suggestion{'s' if report.info_count != 1 else ''}[/blue]"
        if not errors_only else "",
        "",
        f"Submission ready: {ready_str}",
    ]
    body = "\n".join(ln for ln in summary_lines if ln != "" or not errors_only)
    border = "green" if report.submission_ready else "red"
    console.print(Panel(body, title=f"[bold]Review: {module_name}[/bold]", border_style=border))

    if report.submission_ready:
        console.print("[green]✓ No blocking issues — module looks ready for submission[/green]")
        console.print(
            "[dim]Run 'nf-core modules lint' and 'nf-test' as final checks[/dim]"
        )
        return

    # Item list grouped by severity order
    severity_order = ["ERROR"] if errors_only else ["ERROR", "WARNING", "INFO"]
    for item in report.items:
        if item.severity not in severity_order:
            continue
        icon = _ICON[item.severity]
        colour = {"ERROR": "red", "WARNING": "yellow", "INFO": "blue"}[item.severity]
        console.print(
            f"[{colour}]{icon}[/{colour}] [{colour}][{item.category}][/{colour}]"
            f"  {item.message}"
        )


# ── Public API ─────────────────────────────────────────────────────────────────


def review_module(
    module_path: Path,
    standards: Standards,
    console: Console | None = None,
    errors_only: bool = False,
) -> ReviewReport:
    """Run all static analysis checks on a module directory.

    Never runs subprocesses. Returns a ReviewReport.
    """
    if console is None:
        console = Console()

    main_nf_text = _read(module_path / "main.nf")
    meta_yml_path = module_path / "meta.yml"
    env_yml_path = module_path / "environment.yml"

    all_items: list[ReviewItem] = []
    all_items.extend(_check_channel_naming(main_nf_text))
    all_items.extend(_check_process_label(main_nf_text, standards))
    all_items.extend(_check_ext_args(main_nf_text))
    all_items.extend(_check_meta_yml(meta_yml_path, main_nf_text, standards))
    all_items.extend(_check_versions(main_nf_text))
    all_items.extend(_check_conda(env_yml_path, standards))

    error_count = sum(1 for i in all_items if i.severity == "ERROR")
    warning_count = sum(1 for i in all_items if i.severity == "WARNING")
    info_count = sum(1 for i in all_items if i.severity == "INFO")

    report = ReviewReport(
        module_path=str(module_path),
        items=all_items,
        error_count=error_count,
        warning_count=warning_count,
        info_count=info_count,
        submission_ready=(error_count == 0),
    )

    _print_report(report, console, errors_only=errors_only)
    return report
