"""Fast post-generate structural checks (no subprocess, no LLM).

Returns a list of QuickLintWarning objects.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from code_to_module.standards.loader import Standards


class QuickLintWarning(BaseModel):
    severity: Literal["error", "warning"]
    check: str
    message: str
    suggestion: str


def _read_file(path: Path) -> str | None:
    """Read a file; return None if it does not exist."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _extract_section(content: str, section: str) -> str:
    """Return the text of a named Nextflow section (e.g. 'script', 'output').

    Looks for lines starting with optional whitespace + 'section:' and collects
    everything until the next section keyword at the same or lesser indentation.
    """
    section_keywords = {
        "input", "output", "script", "shell", "exec", "stub",
        "when", "label", "container", "cpus", "memory", "time",
        "process", "workflow", "channel",
    }
    lines = content.splitlines()
    in_section = False
    collected: list[str] = []
    section_indent: int | None = None

    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if not in_section:
            if stripped.rstrip(":").lower() == section.lower() and stripped.endswith(":"):
                in_section = True
                section_indent = indent
            continue

        # Stop when we hit another top-level keyword at same or less indentation
        keyword = stripped.split(":")[0].strip().lower()
        if (
            section_indent is not None
            and indent <= section_indent
            and keyword in section_keywords
            and stripped.endswith(":")
        ):
            break
        collected.append(line)

    return "\n".join(collected)


def _has_file_outputs(content: str) -> bool:
    """Return True if the output: section contains at least one path(...) channel."""
    output_section = _extract_section(content, "output")
    return bool(re.search(r"\bpath\s*\(", output_section))


def _extract_container_line(content: str) -> str | None:
    """Return the value on the container directive line, or None if absent."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("container "):
            return stripped[len("container "):].strip()
    return None


def _extract_label(content: str) -> str | None:
    """Return the label value from main.nf, or None if absent."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("label "):
            value = stripped[len("label "):].strip().strip("'\"")
            return value
    return None


def quick_lint(module_dir: Path, standards: Standards) -> list[QuickLintWarning]:
    """Run structural checks on a generated module directory.

    Returns a list of QuickLintWarning objects; empty list means all checks passed.
    """
    issues: list[QuickLintWarning] = []

    main_nf = module_dir / "main.nf"
    meta_yml = module_dir / "meta.yml"

    main_content = _read_file(main_nf)
    meta_content = _read_file(meta_yml)

    if main_content is not None:
        # ── missing_container ────────────────────────────────────────────────
        container_val = _extract_container_line(main_content)
        if container_val is None or "TODO" in container_val or container_val in ("", '""', "''"):
            issues.append(QuickLintWarning(
                severity="error",
                check="missing_container",
                message="Container line is missing or contains TODO — module cannot be submitted without a valid container.",
                suggestion="Run with --container biocontainers or check Dockerfile is accessible.",
            ))

        # ── missing_ext_args ─────────────────────────────────────────────────
        script_section = _extract_section(main_content, "script")
        if "task.ext.args" not in script_section:
            issues.append(QuickLintWarning(
                severity="error",
                check="missing_ext_args",
                message="task.ext.args is not used in the script: block — module params cannot be customised via conf/modules.config.",
                suggestion=f"Add: {standards.ext_args_pattern} to the script: block.",
            ))

        # ── missing_ext_prefix ───────────────────────────────────────────────
        if _has_file_outputs(main_content) and "task.ext.prefix" not in main_content:
            issues.append(QuickLintWarning(
                severity="warning",
                check="missing_ext_prefix",
                message="Module has file outputs but task.ext.prefix is not used — output filenames cannot be customised.",
                suggestion="Add: def prefix = task.ext.prefix ?: '${meta.id}' for named outputs.",
            ))

        # ── wrong_label ──────────────────────────────────────────────────────
        label_val = _extract_label(main_content)
        if label_val is not None and label_val not in standards.valid_labels:
            issues.append(QuickLintWarning(
                severity="error",
                check="wrong_label",
                message=f"Label '{label_val}' is not a valid nf-core process label.",
                suggestion=f"Valid labels: {', '.join(standards.valid_labels)}",
            ))

        # ── missing_versions_topic ───────────────────────────────────────────
        if standards.versions_use_topic_channels and "topic: 'versions'" not in main_content:
            issues.append(QuickLintWarning(
                severity="error",
                check="missing_versions_topic",
                message="versions emit channel is missing topic: 'versions' — required by nf-core 3.5+.",
                suggestion="Add topic: 'versions' to the versions emit channel.",
            ))

    # ── meta_yml_missing_field ───────────────────────────────────────────────
    if meta_content is not None:
        for field in standards.meta_yml_required_fields:
            # Check for top-level YAML key (starts at column 0 with optional space)
            if not re.search(rf"^{re.escape(field)}\s*:", meta_content, re.MULTILINE):
                issues.append(QuickLintWarning(
                    severity="warning",
                    check="meta_yml_missing_field",
                    message=f"Required field '{field}' is missing from meta.yml.",
                    suggestion=f"Add '{field}:' section to meta.yml.",
                ))

    return issues
