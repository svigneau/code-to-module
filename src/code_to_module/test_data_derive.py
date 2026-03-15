"""Plan test data derivation strategies for channels with no direct match."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from code_to_module.models import ChannelSpec, TestDataSource
from code_to_module.standards import Standards

_PR_INSTRUCTIONS = (
    "Run tests/derive_test_data.sh and PR the output files to nf-core/test-datasets."
)


class DerivationPlan(BaseModel):
    """Describes how to obtain test data for a channel that has no direct match."""

    channel_name: str
    strategy: Literal["derive", "chain", "stub"]
    format_tag: str
    template_name: str | None = None
    template_vars: dict[str, Any] = {}
    output_files: list[str] = []
    tool_requirements: list[str] = []
    setup_module: str | None = None
    setup_process_name: str | None = None
    setup_input_path: str | None = None
    setup_output_channel: str | None = None
    source: TestDataSource
    notes: str
    pr_instructions: str | None = None


def _resolve_source_url(source_id: str, standards: Standards) -> str:
    """Return the full URL for the first path of a test_data_index entry."""
    base = standards.test_data_base_path.rstrip("/")
    for entry in standards.test_data_index:
        if entry.get("id") == source_id:
            paths = entry.get("paths", [])
            if paths:
                return f"{base}/modules/{paths[0]}"
    return ""


def _template_source_var(template_name: str) -> str:
    """Derive the Jinja2 source variable name from a template filename.

    "derive_vcf.sh.j2" -> "source_vcf"
    """
    fmt = template_name.replace("derive_", "").replace(".sh.j2", "")
    return f"source_{fmt}"


def plan_derivation(
    channel: ChannelSpec,
    standards: Standards,
    tool_name: str,
) -> DerivationPlan:
    """Plan how to obtain test data for a channel with no direct test-datasets match.

    Priority: DERIVE > CHAIN (fast=true only) > STUB.
    Always returns a DerivationPlan (never None).
    """
    format_tag = channel.format_tags[0] if channel.format_tags else ""
    tag_set = set(channel.format_tags)

    # ── Priority 1: DERIVE ───────────────────────────────────────────────────
    if channel.test_data_strategy not in ("custom", "unknown"):
        for _key, tmpl_entry in standards.derivation_templates.items():
            applicable = set(tmpl_entry.get("applicable_tags", []))
            if tag_set & applicable:
                template_name = tmpl_entry["template"]
                source_id = tmpl_entry["source_id"]
                source_url = _resolve_source_url(source_id, standards)
                source_var = _template_source_var(template_name)
                output_pattern = tmpl_entry.get("output_pattern", f"{tool_name}.dat")
                output_file = output_pattern.replace("{sample_id}", tool_name)
                return DerivationPlan(
                    channel_name=channel.name,
                    strategy="derive",
                    format_tag=format_tag,
                    template_name=template_name,
                    template_vars={
                        source_var: source_url,
                        "sample_id": tool_name,
                    },
                    output_files=[output_file],
                    tool_requirements=tmpl_entry.get("tool_requirements", []),
                    source=TestDataSource.DERIVED,
                    notes=f"subset {source_id} \u2192 PR to nf-core/test-datasets",
                    pr_instructions=_PR_INSTRUCTIONS,
                )

    # ── Priority 2: CHAIN (fast modules only) ────────────────────────────────
    if tag_set:
        for _key, cm in standards.chain_modules.items():
            if not cm.get("fast", False):
                continue
            produces = set(cm.get("produces_tags", []))
            if tag_set.issubset(produces):
                return DerivationPlan(
                    channel_name=channel.name,
                    strategy="chain",
                    format_tag=format_tag,
                    output_files=[],
                    tool_requirements=[],
                    setup_module=cm["module"],
                    setup_process_name=cm["process_name"],
                    setup_input_path=cm["test_input_path"],
                    setup_output_channel=cm["output_channel"],
                    source=TestDataSource.CHAINED,
                    notes=f"{cm['process_name']} setup{{}} block \u2014 no file needed",
                )

    # ── Priority 3: STUB ─────────────────────────────────────────────────────
    return DerivationPlan(
        channel_name=channel.name,
        strategy="stub",
        format_tag=format_tag,
        output_files=[],
        tool_requirements=[],
        source=TestDataSource.STUB,
        notes="stub: mode \u2014 no nf-core test data available",
    )
