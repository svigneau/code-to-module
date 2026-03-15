"""Propose and apply fixes for nf-core module lint/test failures.

Fix safety rules (from CLAUDE.md):
  - NEVER write files without explicit human approval (apply_fix only called after approval).
  - Fix source (RULE vs LLM) is always visible in ProposedFix.
  - Class A: rule-based, deterministic. Class B: LLM-assisted, labelled clearly.
  - Class C: never fixed automatically — explained and stopped.
  - apply_fix() is a dumb writer; the caller is responsible for approval gating.
"""

from __future__ import annotations

import difflib
import re
from enum import Enum
from pathlib import Path

import anthropic
from pydantic import BaseModel

from code_to_module.standards.loader import Standards
from code_to_module.validate import FixClass, LintFailure, NfTestFailure, TestReport

_ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
_MAX_TOKENS = 500

# ── Models ─────────────────────────────────────────────────────────────────────


class FixSource(str, Enum):
    RULE = "RULE"
    LLM = "LLM"


class ProposedFix(BaseModel):
    fix_source: FixSource
    fix_class: FixClass
    description: str
    file_path: str
    diff: str
    line_start: int = 0
    line_end: int = 0
    approved: bool = True
    invalidated: bool = False
    invalidation_reason: str = ""
    is_deletion: bool = False
    # Full file content after applying; used by apply_fix().
    # Empty for deletion fixes (file is removed instead).
    new_file_content: str = ""


# ── Diff helpers ───────────────────────────────────────────────────────────────


def _make_diff(path: Path, old: str, new: str) -> str:
    """Return a unified diff string (for display only)."""
    name = path.name
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=name,
            tofile=name,
        )
    )


def _line_of(content: str, char_offset: int) -> int:
    """Return 1-based line number of char_offset in content."""
    return content[:char_offset].count("\n") + 1


def _apply_unified_diff(original: str, unified_diff: str) -> str | None:
    """Apply a unified diff to original text.

    Returns the patched string, or None if the diff cannot be applied cleanly.
    Handles single-hunk and multi-hunk patches.
    """
    diff_lines = unified_diff.splitlines()
    i = 0
    # Skip file headers
    while i < len(diff_lines) and (
        diff_lines[i].startswith("---")
        or diff_lines[i].startswith("+++")
        or diff_lines[i].startswith("diff ")
    ):
        i += 1

    hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    original_lines = original.splitlines(keepends=True)
    result = list(original_lines)
    offset = 0

    while i < len(diff_lines):
        m = hunk_re.match(diff_lines[i])
        if not m:
            i += 1
            continue

        old_start = int(m.group(1)) - 1  # 0-based
        i += 1

        removes: list[str] = []
        adds: list[str] = []
        while i < len(diff_lines) and not hunk_re.match(diff_lines[i]):
            dl = diff_lines[i]
            if dl.startswith("-"):
                removes.append(dl[1:])
            elif dl.startswith("+"):
                adds.append(dl[1:])
            i += 1

        actual_start = old_start + offset
        actual_end = actual_start + len(removes)

        # Verify removed lines match
        for j, r in enumerate(removes):
            idx = actual_start + j
            if idx >= len(result):
                return None
            if result[idx].rstrip("\r\n") != r.rstrip("\r\n"):
                return None

        add_lines = [(a if a.endswith("\n") else a + "\n") for a in adds]
        result[actual_start:actual_end] = add_lines
        offset += len(adds) - len(removes)

    return "".join(result)


# ── YAML helpers ───────────────────────────────────────────────────────────────


def _load_meta_yml_raw(module_path: Path) -> tuple[dict, str] | tuple[None, None]:
    """Load meta.yml and return (parsed_dict, raw_text). Returns (None, None) on failure."""
    meta_file = module_path / "meta.yml"
    if not meta_file.exists():
        return None, None
    try:
        from ruamel.yaml import YAML

        yaml = YAML()
        raw = meta_file.read_text()
        data = yaml.load(raw)
        return data or {}, raw
    except Exception:
        return None, None


# ── Class A rule functions ─────────────────────────────────────────────────────


def _rule_missing_topic(
    _failure: LintFailure, module_path: Path, _standards: Standards
) -> ProposedFix | None:
    """Add topic: 'versions' to the versions emit channel."""
    main_nf = module_path / "main.nf"
    if not main_nf.exists():
        return None
    content = main_nf.read_text()

    # Match the versions emit line that lacks a topic tag.
    # Handles both:  emit: versions   and  emit: versions, optional: true  (no topic yet)
    pattern = re.compile(
        r"""(path\s+["']versions\.yml["'][^"\n]*?emit:\s*versions\b)(?!\s*,\s*topic)""",
        re.IGNORECASE,
    )
    m = pattern.search(content)
    if not m:
        return None

    old_segment = m.group(0)
    new_segment = old_segment + ", topic: 'versions'"
    new_content = content.replace(old_segment, new_segment, 1)

    return ProposedFix(
        fix_source=FixSource.RULE,
        fix_class=FixClass.CLASS_A,
        description="Add topic: 'versions' to versions channel emit",
        file_path=str(main_nf),
        diff=_make_diff(main_nf, content, new_content),
        line_start=_line_of(content, m.start()),
        line_end=_line_of(content, m.end()),
        new_file_content=new_content,
    )


def _rule_missing_ext_args(
    _failure: LintFailure, module_path: Path, standards: Standards
) -> ProposedFix | None:
    """Insert task.ext.args pattern at the top of the script: block."""
    main_nf = module_path / "main.nf"
    if not main_nf.exists():
        return None
    content = main_nf.read_text()

    if "task.ext.args" in content:
        return None  # already present

    # Find opening triple-quote of script: block
    m = re.search(r'(script:\s*\n[ \t]*""")[ \t]*\n', content)
    if not m:
        return None

    insert_pos = m.end()
    ext_line = f"    {standards.ext_args_pattern}\n"
    new_content = content[:insert_pos] + ext_line + content[insert_pos:]

    return ProposedFix(
        fix_source=FixSource.RULE,
        fix_class=FixClass.CLASS_A,
        description="Add task.ext.args pattern to script block",
        file_path=str(main_nf),
        diff=_make_diff(main_nf, content, new_content),
        line_start=_line_of(content, m.start()),
        line_end=_line_of(content, m.end()),
        new_file_content=new_content,
    )


def _rule_wrong_container_prefix(
    _failure: LintFailure, module_path: Path, _standards: Standards
) -> ProposedFix | None:
    """Replace docker.io/biocontainers/ with quay.io/biocontainers/."""
    main_nf = module_path / "main.nf"
    if not main_nf.exists():
        return None
    content = main_nf.read_text()

    if "docker.io/biocontainers/" not in content:
        return None

    new_content = content.replace("docker.io/biocontainers/", "quay.io/biocontainers/")
    m = re.search(r"docker\.io/biocontainers/", content)
    line = _line_of(content, m.start()) if m else 0

    return ProposedFix(
        fix_source=FixSource.RULE,
        fix_class=FixClass.CLASS_A,
        description="Fix container URL: use quay.io/biocontainers",
        file_path=str(main_nf),
        diff=_make_diff(main_nf, content, new_content),
        line_start=line,
        line_end=line,
        new_file_content=new_content,
    )


def _rule_meta_yml_missing_field(
    failure: LintFailure, module_path: Path, standards: Standards
) -> ProposedFix | None:
    """Insert TODO stub for each missing required meta.yml field."""
    meta_file = module_path / "meta.yml"
    if not meta_file.exists():
        return None
    content = meta_file.read_text()

    # Determine which fields are missing
    data, _ = _load_meta_yml_raw(module_path)
    if data is None:
        return None

    missing = [f for f in standards.meta_yml_required_fields if f not in data]
    if not missing:
        # Try to extract field name from failure message
        for field in standards.meta_yml_required_fields:
            if field in failure.message and field not in content:
                missing = [field]
                break
    if not missing:
        return None

    field = missing[0]
    stub = f"{field}: 'TODO: add {field}'\n"
    # Insert at the end of the file
    new_content = content.rstrip("\n") + "\n" + stub

    return ProposedFix(
        fix_source=FixSource.RULE,
        fix_class=FixClass.CLASS_A,
        description=f"Add missing meta.yml field: {field}",
        file_path=str(meta_file),
        diff=_make_diff(meta_file, content, new_content),
        line_start=content.count("\n") + 1,
        line_end=content.count("\n") + 1,
        new_file_content=new_content,
    )


def _rule_emit_name_mismatch(
    failure: LintFailure, module_path: Path, _standards: Standards
) -> ProposedFix | None:
    """Fix emit name in main.nf to match the canonical name from meta.yml."""
    main_nf = module_path / "main.nf"
    if not main_nf.exists():
        return None
    content = main_nf.read_text()

    data, _ = _load_meta_yml_raw(module_path)
    if not data:
        return None

    meta_outputs: list[str] = []
    for entry in data.get("output", []) or []:
        if isinstance(entry, dict):
            meta_outputs.extend(entry.keys())

    # Extract emit names from main.nf
    emit_names = re.findall(r"emit:\s*(\w+)", content)

    for emit_name in emit_names:
        if emit_name == "versions":
            continue
        # Check if it's not in meta_outputs
        for meta_name in meta_outputs:
            if meta_name != emit_name and meta_name not in ("versions",):
                # Check if the failure message mentions this mismatch
                if emit_name in failure.message or meta_name in failure.message:
                    old_emit = f"emit: {emit_name}"
                    new_emit = f"emit: {meta_name}"
                    new_content = content.replace(old_emit, new_emit, 1)
                    m = re.search(re.escape(old_emit), content)
                    line = _line_of(content, m.start()) if m else 0
                    return ProposedFix(
                        fix_source=FixSource.RULE,
                        fix_class=FixClass.CLASS_A,
                        description=f"Fix emit name: '{emit_name}' → '{meta_name}' to match meta.yml",
                        file_path=str(main_nf),
                        diff=_make_diff(main_nf, content, new_content),
                        line_start=line,
                        line_end=line,
                        new_file_content=new_content,
                    )
    return None


def _rule_stale_snapshot(
    failure: NfTestFailure, module_path: Path, _standards: Standards
) -> ProposedFix | None:
    """Propose deletion of the stale .snap file."""
    snap_dir = module_path / "tests"
    if not snap_dir.exists():
        return None
    snaps = list(snap_dir.glob("*.snap"))
    if not snaps:
        return None

    snap_file = snaps[0]
    return ProposedFix(
        fix_source=FixSource.RULE,
        fix_class=FixClass.CLASS_A,
        description="Snapshot deleted — will be regenerated on next nf-test run",
        file_path=str(snap_file),
        diff=f"[delete] {snap_file.name}",
        is_deletion=True,
        new_file_content="",
    )


# ── Class B LLM fix functions ──────────────────────────────────────────────────


def _extract_process_name(content: str) -> str:
    m = re.search(r"process\s+(\w+)\s*\{", content)
    return m.group(1) if m else "UNKNOWN"


def _extract_script_block(content: str) -> str:
    m = re.search(r'script:\s*\n([ \t]*""".*?""")', content, re.DOTALL)
    return m.group(1) if m else ""


def _llm_call(system: str, user: str) -> str:
    """Call the Anthropic API and return the text response."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text.strip()


def _llm_fix_output_pattern(
    failure: LintFailure | NfTestFailure,
    module_path: Path,
    _standards: Standards,
) -> ProposedFix:
    """Ask LLM to fix an incorrect output filename glob pattern."""
    main_nf = module_path / "main.nf"
    content = main_nf.read_text() if main_nf.exists() else ""
    process_name = _extract_process_name(content)
    script_block = _extract_script_block(content)

    data, _ = _load_meta_yml_raw(module_path)
    pattern_hint = ""
    if data:
        for entry in data.get("output", []) or []:
            if isinstance(entry, dict):
                for ch_data in entry.values():
                    if isinstance(ch_data, dict) and "pattern" in ch_data:
                        pattern_hint = ch_data["pattern"]
                        break

    error_msg = failure.error if isinstance(failure, NfTestFailure) else failure.message

    system = "You are fixing an nf-core module. Return ONLY a unified diff, nothing else."
    user = (
        f"The nf-test for process {process_name} failed because the output file was not found.\n"
        f"Expected pattern: {pattern_hint or '(unknown)'}\n"
        f"Actual nf-test error: {error_msg}\n\n"
        f"Script block from main.nf:\n{script_block}\n\n"
        "The output glob pattern in main.nf is wrong. Provide a unified diff that fixes "
        "only the pattern: line in the output block of main.nf.\n"
        "Return ONLY the unified diff, starting with ---."
    )

    return _parse_llm_diff_response(
        _llm_call(system, user),
        main_nf,
        content,
        "Fix wrong output filename pattern",
        FixClass.CLASS_B,
    )


def _llm_fix_label(
    failure: LintFailure,
    module_path: Path,
    standards: Standards,
) -> ProposedFix:
    """Ask LLM to fix an inappropriate process label."""
    main_nf = module_path / "main.nf"
    content = main_nf.read_text() if main_nf.exists() else ""
    process_name = _extract_process_name(content)

    current_label_m = re.search(r"label\s+'([^']+)'", content)
    current_label = current_label_m.group(1) if current_label_m else "(unknown)"

    label_table = "\n".join(
        f"  {label}: {r['cpus']} CPUs, {r['memory_gb']} GB RAM, {r['time_h']}h"
        for label, r in standards.label_resources.items()
    )

    system = "You are fixing an nf-core module. Return ONLY a unified diff, nothing else."
    user = (
        f"The process label for {process_name} is '{current_label}'.\n"
        f"The lint check suggests this is inappropriate.\n"
        f"Lint message: {failure.message}\n\n"
        f"Valid labels and their resource limits:\n{label_table}\n\n"
        "Based on the tool name and lint message, what is the correct label?\n"
        "Provide a unified diff that changes only the label: line in main.nf.\n"
        "Return ONLY the unified diff, starting with ---."
    )

    return _parse_llm_diff_response(
        _llm_call(system, user),
        main_nf,
        content,
        "Fix inappropriate process label",
        FixClass.CLASS_B,
    )


def _llm_fix_channel_description(
    failure: LintFailure,
    module_path: Path,
    _standards: Standards,
) -> ProposedFix:
    """Ask LLM to fix an incomplete channel description in meta.yml."""
    meta_file = module_path / "meta.yml"
    meta_content = meta_file.read_text() if meta_file.exists() else ""

    # Extract channel name from failure message if possible
    channel_m = re.search(r"channel[:\s]+['\"]?(\w+)['\"]?", failure.message, re.I)
    channel_name = channel_m.group(1) if channel_m else "(unknown)"

    data, _ = _load_meta_yml_raw(module_path)
    current_desc = ""
    pattern = ""
    tool_name = ""
    if data:
        tool_name = next(iter(data.get("tools", [{}])[0] if data.get("tools") else [{}]), "")
        for section in ("input", "output"):
            for entry in data.get(section, []) or []:
                if isinstance(entry, dict) and channel_name in entry:
                    ch = entry[channel_name]
                    if isinstance(ch, dict):
                        current_desc = ch.get("description", "")
                        pattern = ch.get("pattern", "")

    system = "You are fixing an nf-core module. Return ONLY a unified diff, nothing else."
    user = (
        f"The channel '{channel_name}' in meta.yml has an incomplete description: '{current_desc}'\n"
        f"The channel pattern is: '{pattern}'\n"
        f"The tool is: {tool_name}\n\n"
        "Write a concise, accurate one-sentence description for this channel.\n"
        "Provide a unified diff that updates only the description: line in meta.yml.\n"
        "Return ONLY the unified diff, starting with ---."
    )

    return _parse_llm_diff_response(
        _llm_call(system, user),
        meta_file,
        meta_content,
        f"Fix incomplete channel description for '{channel_name}'",
        FixClass.CLASS_B,
    )


def _parse_llm_diff_response(
    response_text: str,
    target_file: Path,
    original_content: str,
    description: str,
    fix_class: FixClass,
) -> ProposedFix:
    """Parse an LLM diff response into a ProposedFix.

    If the response doesn't parse as a unified diff or can't be applied,
    returns a ProposedFix with approved=False (degraded to CLASS_C behaviour).
    """
    # Check it looks like a unified diff
    if not re.search(r"^---", response_text, re.MULTILINE):
        return ProposedFix(
            fix_source=FixSource.LLM,
            fix_class=FixClass.CLASS_C,
            description=description,
            file_path=str(target_file),
            diff=response_text[:500],
            approved=False,
            new_file_content="",
        )

    # Try to apply it
    patched = _apply_unified_diff(original_content, response_text)
    if patched is None:
        return ProposedFix(
            fix_source=FixSource.LLM,
            fix_class=FixClass.CLASS_C,
            description=description + " — LLM returned unparseable diff — manual fix required",
            file_path=str(target_file),
            diff=response_text[:500],
            approved=False,
            new_file_content="",
        )

    return ProposedFix(
        fix_source=FixSource.LLM,
        fix_class=fix_class,
        description=description,
        file_path=str(target_file),
        diff=response_text,
        new_file_content=patched,
    )


# ── Dispatch maps ──────────────────────────────────────────────────────────────

_CLASS_A_LINT_RULES = {
    "MODULE_MISSING_VERSIONS_TOPIC": _rule_missing_topic,
    "MODULE_MISSING_EXT_ARGS": _rule_missing_ext_args,
    "MODULE_CONTAINER_URL_FORMAT": _rule_wrong_container_prefix,
    "MODULE_META_YML_MISSING_FIELD": _rule_meta_yml_missing_field,
    "MODULE_EMIT_NAME_MISMATCH": _rule_emit_name_mismatch,
    "MODULE_CONDA_CHANNEL_ORDER": None,  # no rule implemented yet
}

_CLASS_B_LINT_LLM = {
    "MODULE_INCORRECT_OUTPUT_PATTERN": _llm_fix_output_pattern,
    "MODULE_LABEL_INAPPROPRIATE": _llm_fix_label,
    "MODULE_DESCRIPTION_MISSING": _llm_fix_channel_description,
}


# ── propose_fixes ──────────────────────────────────────────────────────────────


def propose_fixes(
    report: TestReport,
    standards: Standards,
) -> list[ProposedFix]:
    """Analyse report and propose all applicable fixes.

    Returns fixes ordered: Class A first, then Class B.
    Class C failures are NOT returned — the caller handles them from the report.
    """
    module_path = Path(report.module_path)
    fixes: list[ProposedFix] = []
    class_b: list[ProposedFix] = []

    # ── Lint failures ──────────────────────────────────────────────────────────
    for lint_failure in report.lint_failures:
        if lint_failure.fix_class == FixClass.CLASS_A:
            rule_fn = _CLASS_A_LINT_RULES.get(lint_failure.code)
            if rule_fn is not None:
                fix = rule_fn(lint_failure, module_path, standards)
                if fix is not None:
                    fixes.append(fix)

        elif lint_failure.fix_class == FixClass.CLASS_B:
            llm_fn = _CLASS_B_LINT_LLM.get(lint_failure.code)
            if llm_fn is not None:
                fix = llm_fn(lint_failure, module_path, standards)
                class_b.append(fix)

    # ── nf-test failures ───────────────────────────────────────────────────────
    for nft_failure in report.nftest_failures:
        if nft_failure.fix_class == FixClass.CLASS_A and nft_failure.error_type == "snapshot_mismatch":
            fix = _rule_stale_snapshot(nft_failure, module_path, standards)
            if fix is not None:
                fixes.append(fix)

        elif nft_failure.fix_class == FixClass.CLASS_B and nft_failure.error_type == "file_not_found":
            fix = _llm_fix_output_pattern(nft_failure, module_path, standards)
            class_b.append(fix)

    # Class A first, then Class B
    return fixes + class_b


# ── apply_fix / apply_approved_fixes ──────────────────────────────────────────


def apply_fix(fix: ProposedFix) -> bool:
    """Write the fix to disk.  Only call after user approval.

    Returns True on success.
    """
    if not fix.approved or fix.invalidated:
        return False
    try:
        path = Path(fix.file_path)
        if fix.is_deletion:
            path.unlink(missing_ok=True)
            return True
        path.write_text(fix.new_file_content, encoding="utf-8")
        return True
    except OSError:
        return False


def _lines_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    if a_start == 0 and a_end == 0:
        return False
    if b_start == 0 and b_end == 0:
        return False
    return not (a_end < b_start or b_end < a_start)


def apply_approved_fixes(fixes: list[ProposedFix]) -> int:
    """Apply all fixes with approved=True in order.

    Implements dependency invalidation: if a fix was applied and a later fix
    targets the same file with overlapping line ranges, mark it invalidated.

    Returns count of successfully applied fixes.
    """
    applied: list[ProposedFix] = []
    count = 0

    for fix in fixes:
        if not fix.approved:
            continue
        if fix.invalidated:
            continue

        # Invalidation check: same file + overlapping lines as any already-applied fix
        for prev in applied:
            if prev.file_path == fix.file_path:
                if fix.is_deletion or prev.is_deletion:
                    fix.invalidated = True
                    fix.invalidation_reason = (
                        f"Invalidated — {Path(fix.file_path).name} was already modified/deleted"
                    )
                    break
                if _lines_overlap(prev.line_start, prev.line_end, fix.line_start, fix.line_end):
                    fix.invalidated = True
                    fix.invalidation_reason = (
                        f"Invalidated by earlier fix (overlapping lines {prev.line_start}–{prev.line_end})"
                    )
                    break

        if fix.invalidated:
            continue

        if apply_fix(fix):
            applied.append(fix)
            count += 1

    return count
