"""Semantic parsing and scoring for nf-core module regression testing.

Exports three things:
  ParsedModule   — parsed fields from main.nf + meta.yml
  parse_module   — extracts those fields using regex (no Nextflow parser)
  RegressionScore + semantic_score — weighted similarity between two modules
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# ── Weights (sum to 1.0) ───────────────────────────────────────────────────────
# process name and container are the highest-value signals; keyword overlap
# is a soft signal that can vary legitimately between implementations.

WEIGHTS: dict[str, float] = {
    "process_name_match": 0.20,
    "container_match": 0.20,
    "output_coverage": 0.20,
    "input_coverage": 0.10,
    "has_versions_emit": 0.10,
    "has_ext_args": 0.10,
    "no_todos": 0.05,
    "keyword_overlap": 0.05,
}

assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "WEIGHTS must sum to 1.0"


# ── Data classes ───────────────────────────────────────────────────────────────


@dataclass
class ParsedModule:
    process_name: str
    container_docker: str
    input_names: list[str]   # channel names in input block, excluding meta
    output_names: list[str]  # emit names in output block, excluding versions
    has_versions_emit: bool
    has_ext_args: bool
    has_todo: bool
    script_tool_name: str    # first word of the tool invocation in script block
    keywords: list[str]      # from meta.yml keywords field


@dataclass
class RegressionScore:
    process_name_match: bool
    container_match: bool
    output_coverage: float
    input_coverage: float
    has_versions_emit: bool
    has_ext_args: bool
    no_todos: bool
    keyword_overlap: float
    overall: float           # weighted mean of all fields above


# ── Internal helpers ───────────────────────────────────────────────────────────


def _extract_docker_url(text: str) -> str:
    """Return the Docker container URL from a main.nf container block."""
    # Standard nf-core ternary: `? singularity_url : docker_url`
    m = re.search(
        r"workflow\.containerEngine\s*==\s*'singularity'.*?\?\s*'([^']+)'\s*:\s*'([^']+)'",
        text,
        re.DOTALL,
    )
    if m:
        return m.group(2)  # group 1 = singularity, group 2 = docker
    # Fallback: simple container "url" (no interpolation)
    m2 = re.search(r"container\s+[\"']([^$\"\n][^\"'\n]*)[\"']", text)
    return m2.group(1).strip() if m2 else ""


def _extract_input_names(text: str) -> list[str]:
    """Return channel names from the input: block, excluding meta."""
    m = re.search(r"^\s*input:\s*\n(.*?)^\s*output:", text, re.MULTILINE | re.DOTALL)
    if not m:
        return []
    block = m.group(1)
    names: list[str] = []

    # tuple val(meta), path/env/val(name) — named capture inside parens
    for tm in re.finditer(r"tuple\s+val\(meta\)[,\s]+\w+\((\w+)", block):
        names.append(tm.group(1))
    # standalone path(name)
    for pm in re.finditer(r"^\s*path\s*\(\s*(\w+)", block, re.MULTILINE):
        names.append(pm.group(1))
    # standalone val(name) — skip meta
    for vm in re.finditer(r"^\s*val\s*\(\s*(\w+)", block, re.MULTILINE):
        if vm.group(1) != "meta":
            names.append(vm.group(1))

    # deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            result.append(n)
    return result


def _extract_output_names(text: str) -> list[str]:
    """Return emit names from the output: block, excluding 'versions'."""
    m = re.search(
        r"^\s*output:\s*\n(.*?)(?:^\s*(?:when|script|stub):)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not m:
        return []
    block = m.group(1)
    return [
        em.group(1)
        for em in re.finditer(r"emit:\s*(\w+)", block)
        if em.group(1) != "versions"
    ]


def _extract_script_tool_name(text: str) -> str:
    """Return the first tool name found in the script block."""
    m = re.search(r"\bscript:\b.*?\"\"\"(.*?)\"\"\"", text, re.DOTALL)
    if not m:
        return ""
    script = m.group(1)
    for line in script.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("//", "#", "def ", "TOOL_VERSION", "cat ", "echo ")):
            continue
        word = re.match(r"([\w][\w-]*)", line)
        if word:
            return word.group(1)
    return ""


def _extract_keywords(meta_yml: Path) -> list[str]:
    """Return the keywords list from meta.yml, or [] on any parse error."""
    try:
        data = yaml.safe_load(meta_yml.read_text()) or {}
        return [str(k) for k in (data.get("keywords") or [])]
    except Exception:
        return []


# ── Public API ─────────────────────────────────────────────────────────────────


def parse_module(module_path: Path) -> ParsedModule:
    """Parse main.nf and meta.yml from *module_path* into a ParsedModule.

    Raises ValueError if either required file is missing.
    Uses regex throughout — not a full Nextflow parser.
    """
    main_nf = module_path / "main.nf"
    meta_yml = module_path / "meta.yml"

    if not main_nf.exists():
        raise ValueError(f"main.nf not found: {main_nf}")
    if not meta_yml.exists():
        raise ValueError(f"meta.yml not found: {meta_yml}")

    text = main_nf.read_text()

    # Process name
    pm = re.search(r"^process\s+(\w+)\s*\{", text, re.MULTILINE)
    process_name = pm.group(1) if pm else ""

    return ParsedModule(
        process_name=process_name,
        container_docker=_extract_docker_url(text),
        input_names=_extract_input_names(text),
        output_names=_extract_output_names(text),
        has_versions_emit=bool(re.search(r"emit:\s*versions", text)),
        has_ext_args="task.ext.args" in text,
        has_todo="TODO" in text,
        script_tool_name=_extract_script_tool_name(text),
        keywords=_extract_keywords(meta_yml),
    )


def _coverage(generated: list[str], reference: list[str]) -> float:
    """Fraction of reference items present in generated. 1.0 if reference is empty."""
    if not reference:
        return 1.0
    return len(set(generated) & set(reference)) / len(set(reference))


def _jaccard(a: list[str], b: list[str]) -> float:
    """Jaccard similarity of two lists. 1.0 if both are empty."""
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return 1.0
    return len(sa & sb) / len(union)


def semantic_score(generated: ParsedModule, reference: ParsedModule) -> RegressionScore:
    """Compute a weighted semantic similarity score between *generated* and *reference*."""
    pn_match = generated.process_name == reference.process_name
    ct_match = generated.container_docker == reference.container_docker
    out_cov = _coverage(generated.output_names, reference.output_names)
    in_cov = _coverage(generated.input_names, reference.input_names)
    has_ver = generated.has_versions_emit
    has_ext = generated.has_ext_args
    no_todo = not generated.has_todo
    kw_overlap = _jaccard(generated.keywords, reference.keywords)

    overall = (
        WEIGHTS["process_name_match"] * float(pn_match)
        + WEIGHTS["container_match"] * float(ct_match)
        + WEIGHTS["output_coverage"] * out_cov
        + WEIGHTS["input_coverage"] * in_cov
        + WEIGHTS["has_versions_emit"] * float(has_ver)
        + WEIGHTS["has_ext_args"] * float(has_ext)
        + WEIGHTS["no_todos"] * float(no_todo)
        + WEIGHTS["keyword_overlap"] * kw_overlap
    )

    return RegressionScore(
        process_name_match=pn_match,
        container_match=ct_match,
        output_coverage=out_cov,
        input_coverage=in_cov,
        has_versions_emit=has_ver,
        has_ext_args=has_ext,
        no_todos=no_todo,
        keyword_overlap=kw_overlap,
        overall=overall,
    )
