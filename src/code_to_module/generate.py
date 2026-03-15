"""Render Jinja2 templates into nf-core module files."""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from code_to_module.models import ChannelSpec, ExistingModule, ModuleSpec, TestSpec

_TEMPLATES_DIR = Path(__file__).parent / "templates"


# ── Jinja2 filters ─────────────────────────────────────────────────────────────


def _stub_filenames_for_channel(ch: ChannelSpec) -> list[str]:
    """Return one or more touch-target filenames for a stub block.

    Derives names from the channel's glob pattern (or falls back to *.{name}).
    Skips val-type channels (no files to touch).
    Handles brace expansion: *.{png,pdf} → [${prefix}.png, ${prefix}.pdf]
    """
    if ch.type not in ("map", "file"):
        return []
    effective = ch.pattern if ch.pattern else f"*.{ch.name}"
    # Strip leading wildcard(s) to get the suffix
    suffix = effective.lstrip("*")
    # Handle brace expansion: .{ext1,ext2,...}
    m = re.match(r"(.*?)\{([^}]+)\}(.*)", suffix)
    if m:
        prefix_part, exts_str, tail = m.group(1), m.group(2), m.group(3)
        return [
            f"${{prefix}}{prefix_part}{ext.strip()}{tail}"
            for ext in exts_str.split(",")
        ]
    return [f"${{prefix}}{suffix}"]


def _tokenize_shell(cmd: str) -> list[str]:
    """Split a shell command on spaces, preserving ${...} Nextflow interpolations."""
    tokens: list[str] = []
    current: list[str] = []
    depth = 0
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if c == "$" and i + 1 < len(cmd) and cmd[i + 1] == "{":
            depth += 1
            current.append("${")
            i += 2
            continue
        if depth > 0:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            current.append(c)
        elif c == " ":
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(c)
        i += 1
    if current:
        tokens.append("".join(current))
    return [t for t in tokens if t]


def _combine_flags(tokens: list[str]) -> list[str]:
    """Pair -flag value tokens into single strings."""
    result: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-") and i + 1 < len(tokens):
            nxt = tokens[i + 1]
            # Pair flag with its value if value is not itself a flag
            if not nxt.startswith("-"):
                result.append(f"{tok} {nxt}")
                i += 2
                continue
        result.append(tok)
        i += 1
    return result


def _format_script_command(cmd: str) -> str:
    """Format a shell command with backslash continuation when it has >2 args.

    Tokenises the command respecting ${...} Nextflow interpolations, pairs
    -flag value tokens, then joins with '\\\\\\n        ' when there are more
    than 2 argument groups (so the result renders as backslash-newline in the
    Nextflow script block).
    """
    cmd = cmd.strip()
    if not cmd:
        return cmd
    raw_tokens = _tokenize_shell(cmd)
    if not raw_tokens:
        return cmd
    executable = raw_tokens[0]
    arg_tokens = _combine_flags(raw_tokens[1:])
    if len(arg_tokens) <= 2:
        return cmd
    continuation = " \\\n        "
    return executable + continuation + continuation.join(arg_tokens)


def _make_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["stub_filenames"] = _stub_filenames_for_channel
    env.filters["format_script_command"] = _format_script_command
    return env


def generate(
    spec: ModuleSpec,
    test_spec: TestSpec,
    outdir: Path,
    existing_modules: list[ExistingModule] | None = None,
    console: Console | None = None,
) -> list[Path]:
    """Render all module templates and write files to outdir/{tool_name}/.

    Returns the list of every path created.
    """
    if console is None:
        console = Console()

    env = _make_env()
    tool_dir = outdir / spec.tool_name
    tests_dir = tool_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []

    template_jobs: list[tuple[str, Path]] = [
        ("main.nf.j2", tool_dir / "main.nf"),
        ("meta.yml.j2", tool_dir / "meta.yml"),
        ("environment.yml.j2", tool_dir / "environment.yml"),
        ("tests/main.nf.test.j2", tests_dir / "main.nf.test"),
    ]

    ctx = {"spec": spec, "test_spec": test_spec}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task_id = progress.add_task("Generating module files…", total=None)

        for template_name, dest_path in template_jobs:
            progress.update(task_id, description=f"Writing {dest_path.name}…")
            tmpl = env.get_template(template_name)
            dest_path.write_text(tmpl.render(**ctx), encoding="utf-8")
            created.append(dest_path)

        # Write Dockerfile from spec content if provided
        if spec.dockerfile_content is not None:
            progress.update(task_id, description="Writing Dockerfile…")
            dockerfile_path = tool_dir / "Dockerfile"
            dockerfile_path.write_text(spec.dockerfile_content, encoding="utf-8")
            created.append(dockerfile_path)

        # Write derivation script + data placeholder
        if test_spec.needs_derivation and test_spec.derivation_script_content:
            progress.update(task_id, description="Writing derive_test_data.sh…")
            script_path = tests_dir / "derive_test_data.sh"
            script_path.write_text(
                test_spec.derivation_script_content, encoding="utf-8"
            )
            script_path.chmod(script_path.stat().st_mode | 0o111)  # chmod +x
            created.append(script_path)

            gitkeep = tests_dir / "data" / ".gitkeep"
            gitkeep.parent.mkdir(exist_ok=True)
            gitkeep.touch()
            created.append(gitkeep)

    # Consistency check against sibling modules
    if existing_modules:
        _run_consistency_check(spec, existing_modules, console)

    return created


# ── Consistency check ──────────────────────────────────────────────────────────


def render_module(spec: ModuleSpec) -> str:
    """Render main.nf for *spec* and return the content as a string (no disk I/O)."""
    env = _make_env()
    tmpl = env.get_template("main.nf.j2")
    return tmpl.render(spec=spec)


def _run_consistency_check(
    spec: ModuleSpec,
    existing_modules: list[ExistingModule],
    console: Console,
) -> None:
    for em in existing_modules:
        if em.load_error is not None:
            continue

        if em.container_docker and em.container_docker != spec.container_docker:
            console.print(
                Panel(
                    f"[yellow]⚠ Consistency warning: container differs from sibling module[/yellow]\n"
                    f"  New module:     {spec.container_docker}\n"
                    f"  {em.process_name}: {em.container_docker}\n"
                    f"  Review: the new module may need an updated container version, or\n"
                    f"  the sibling module may need updating. Check both before submitting.",
                    border_style="yellow",
                )
            )

        if em.label and em.label != spec.label:
            console.print(
                Panel(
                    f"[yellow]⚠ Consistency warning: label differs from sibling module[/yellow]\n"
                    f"  New module: {spec.label}  •  {em.process_name}: {em.label}\n"
                    f"  Review: confirm the resource requirements are intentionally different.",
                    border_style="yellow",
                )
            )
