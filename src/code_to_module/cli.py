from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_console() -> Console:
    return Console()


def _repo_scan_summary(source: object) -> str:
    """Build the 📂 Repo scan summary line from a CodeSource."""
    from code_to_module.models import CodeSource

    cs: CodeSource = source  # type: ignore[assignment]
    hint = cs.container_hint
    n_files = len(cs.repo_manifest) or 1

    parts = [f"📂 Repo scan: {n_files} file{'s' if n_files != 1 else ''}"]
    if hint:
        parts.append(f"Dockerfile {'✓' if hint.has_dockerfile else '✗'}")
        parts.append(f"environment.yml {'✓' if hint.has_environment_yml else '✗'}")
        parts.append(f"requirements.txt {'✓' if hint.has_requirements_txt else '✗'}")
    return " | ".join(parts)


def _print_version_panel(console: Console) -> None:
    try:
        from importlib.metadata import version

        ver = version("code-to-module")
    except Exception:
        ver = "dev"
    console.print(Panel(f"code-to-module v{ver}", expand=False))


# ── convert ────────────────────────────────────────────────────────────────────


@click.group(name="code-to-module")
def main() -> None:
    """Convert scripts and Git repos into submission-ready nf-core modules."""


@main.command()
@click.argument("source")
@click.option("--outdir", default="./modules", show_default=True, help="Output directory for generated modules.")
@click.option("--tier", type=int, default=None, help="Override complexity tier (1–5).")
@click.option(
    "--container",
    type=click.Choice(["dockerfile", "biocontainers", "generate", "stub"]),
    default=None,
    help="Override container selection.",
)
@click.option("--no-interaction", is_flag=True, default=False, help="Use default container silently (no menu shown).")
@click.option("--dry-run", is_flag=True, default=False, help="Show what would be generated without writing files.")
@click.option("--no-lint", is_flag=True, default=False, help="Skip post-generate lint checks.")
@click.option("--no-update-check", is_flag=True, default=False, help="Skip standards staleness check.")
@click.option("--functionalities", default=None, help="Comma-separated functionality names to generate.")
@click.option("--all-functionalities", is_flag=True, default=False, help="Select all detected functionalities without prompting.")
@click.option(
    "--docs",
    multiple=True,
    metavar="TEXT",
    help="URL or local file path to documentation. Repeatable.",
)
@click.option(
    "--existing-modules",
    multiple=True,
    metavar="PATH",
    help="Path to an existing nf-core module directory. Repeatable.",
)
def convert(
    source: str,
    outdir: str,
    tier: int | None,
    container: str | None,
    no_interaction: bool,
    dry_run: bool,
    no_lint: bool,
    no_update_check: bool,
    functionalities: str | None,
    all_functionalities: bool,
    docs: tuple[str, ...],
    existing_modules: tuple[str, ...],
) -> None:
    """Convert SOURCE (file path or Git URL) into nf-core module(s)."""
    import code_to_module.api as _api
    from code_to_module.ingest import ingest

    console = _make_console()
    _print_version_panel(console)

    # Ingest here to display the repo scan summary before delegating to the API
    code_source = ingest(source, docs=list(docs), existing_modules=list(existing_modules))
    console.print(_repo_scan_summary(code_source))

    # Dry-run: discover only, then short-circuit
    if dry_run:
        import code_to_module.discover as _discover

        with console.status("Scanning for functionalities..."):
            disc_result = _discover.discover(code_source)

        disc_result = _discover.select_functionalities(
            disc_result,
            functionalities_flag=functionalities,
            all_flag=all_functionalities,
            no_interaction=True,
            console=console,
        )
        if not disc_result.selected:
            console.print("[red]No functionalities selected. Exiting.[/red]")
            sys.exit(2)

        names = [s.name for s in disc_result.selected]
        console.print(f"Would generate {len(names)} module(s): {', '.join(names)}")
        console.print("(dry run — no files written)")
        sys.exit(0)

    # Full conversion via API (non-interactive; display is handled here)
    func_filter = functionalities if functionalities else (None if all_functionalities else None)

    with console.status("Converting..."):
        result = _api.convert(
            source=source,
            outdir=outdir,
            tier_override=tier,
            container=container,
            functionalities=func_filter,
            dry_run=False,
            no_lint=no_lint,
            docs=list(docs),
            existing_modules=list(existing_modules),
        )

    if result["error"]:
        console.print(f"[red]Error: {result['error']}[/red]")
        sys.exit(1)

    if not result["functionalities_selected"]:
        console.print("[red]No functionalities selected. Exiting.[/red]")
        sys.exit(2)

    outdir_path = Path(outdir)

    # Display per-module results
    generated_mods = [m for m in result["modules"] if m["files_created"]]

    for mod in result["modules"]:
        console.rule(f" {mod['functionality_name']} ")

        if mod["tier"] == 5 or not mod["files_created"]:
            console.print(
                f"[yellow]⚠ Skipping {mod['functionality_name']} — Tier 5 requires manual module creation.\n"
                f"  See: https://nf-co.re/docs/contributing/modules[/yellow]"
            )
            continue

        for path in mod["files_created"]:
            console.print(f"[green]✓[/green] {path}")

        # Display warnings from API result
        if not no_lint:
            lint_warnings_raw = [w for w in mod["warnings"] if w.startswith("[error]") or w.startswith("[warning]")]
            if not lint_warnings_raw:
                console.print("[green]✓ Quick lint passed[/green]")
            else:
                has_errors = any(w.startswith("[error]") for w in lint_warnings_raw)
                for w in lint_warnings_raw:
                    if w.startswith("[error]"):
                        console.print(f"[red]✗ {w[len('[error] '):]}")
                    else:
                        console.print(f"[yellow]⚠ {w[len('[warning] '):]}")
                if has_errors:
                    console.print("[red]Quick lint found errors — run 'code-to-module fix' after reviewing.[/red]")

    _print_api_summary(result, outdir_path, console)

    if generated_mods:
        sys.exit(0)
    else:
        sys.exit(1)


def _print_api_summary(result: dict, outdir_path: Path, console: Console) -> None:
    lines: list[str] = []

    generated_mods = [m for m in result["modules"] if m["files_created"]]
    skipped_mods = [m for m in result["modules"] if not m["files_created"]]

    gen_count = len(generated_mods)
    skip_count = len(skipped_mods)

    summary = f"✓ Generated {gen_count} module{'s' if gen_count != 1 else ''}"
    if skip_count:
        summary += f"  •  ⚠ Skipped {skip_count} (Tier 5)"
    lines.append(summary)
    lines.append("")

    for mod in generated_mods:
        tool_name = mod.get("module_spec", {}).get("tool_name", mod["functionality_name"])
        tool_dir = outdir_path / tool_name
        n_files = len(mod["files_created"])
        lines.append(
            f"{tool_dir}/   ({n_files} file{'s' if n_files != 1 else ''})   "
            f"{mod['container_source']}  Tier {mod['tier']}"
        )

    for mod in skipped_mods:
        lines.append(f"{mod['functionality_name']}             skipped — Tier 5 — create manually")

    if generated_mods:
        lines.append("")
        lines.append("Next steps for each generated module:")
        lines.append("  1. Review main.nf — check channel types and version capture")

        any_stub = any("TODO" in m["container_docker"] for m in generated_mods)
        step = 2
        if any_stub:
            lines.append(f"  {step}. Fill in container URLs in main.nf (currently TODO)")
            step += 1

        for mod in generated_mods:
            tool_name = mod.get("module_spec", {}).get("tool_name", mod["functionality_name"])
            lines.append(f"  {step}. ctm-validate review {outdir_path / tool_name}")
            step += 1
            lines.append(f"  {step}. ctm-validate test {outdir_path / tool_name}")
            step += 1

    console.print(Panel("\n".join(lines), border_style="green" if generated_mods else "yellow"))


# ── assess-only ────────────────────────────────────────────────────────────────


@main.command("assess-only")
@click.argument("source")
@click.option("--json", "output_json", is_flag=True, default=False, help="Print JSON array to stdout.")
@click.option(
    "--docs",
    multiple=True,
    metavar="TEXT",
    help="URL or local file path to documentation. Repeatable.",
)
@click.option(
    "--existing-modules",
    multiple=True,
    metavar="PATH",
    help="Path to an existing nf-core module directory. Repeatable.",
)
def assess_only(
    source: str,
    output_json: bool,
    docs: tuple[str, ...],
    existing_modules: tuple[str, ...],
) -> None:
    """Detect functionalities and assess complexity tier without generating modules."""
    import code_to_module.assess as _assess
    import code_to_module.discover as _discover
    from code_to_module.ingest import ingest

    console = _make_console() if not output_json else Console(quiet=True)

    code_source = ingest(source, docs=list(docs), existing_modules=list(existing_modules))

    if not output_json:
        console.print(_repo_scan_summary(code_source))

    with console.status("Scanning for functionalities..."):
        disc_result = _discover.discover(code_source)

    results: list[dict] = []
    for func in disc_result.functionalities:
        func_tier, confidence, warns = _assess.assess(
            func, code_source, console if not output_json else None
        )
        results.append({
            "name": func.name,
            "tier": func_tier,
            "confidence": confidence,
            "warnings": warns,
        })

    if output_json:
        click.echo(json.dumps(results, indent=2))
        return

    generatable = sum(1 for r in results if r["tier"] < 5)
    skipped = len(results) - generatable
    console.print(
        f"Summary: {len(results)} functionalities found — "
        f"{generatable} can be generated (Tier 1–4), "
        f"{skipped} need{'s' if skipped == 1 else ''} manual work (Tier 5)"
    )


# ── containers ─────────────────────────────────────────────────────────────────


@main.command("containers")
@click.argument("source")
@click.option("--functionality", "functionality_name", default=None, help="Filter to one functionality by name.")
@click.option("--json", "output_json", is_flag=True, default=False, help="Print JSON array to stdout.")
@click.option(
    "--docs",
    multiple=True,
    metavar="TEXT",
    help="URL or local file path to documentation. Repeatable.",
)
@click.option(
    "--existing-modules",
    multiple=True,
    metavar="PATH",
    help="Path to an existing nf-core module directory. Repeatable.",
)
def containers_cmd(
    source: str,
    functionality_name: str | None,
    output_json: bool,
    docs: tuple[str, ...],
    existing_modules: tuple[str, ...],
) -> None:
    """Discover and display all available container options for SOURCE."""
    import code_to_module.assess as _assess
    import code_to_module.container as _container
    import code_to_module.discover as _discover
    from code_to_module.ingest import ingest

    console = _make_console() if not output_json else Console(quiet=True)

    code_source = ingest(source, docs=list(docs), existing_modules=list(existing_modules))

    if not output_json:
        console.print(_repo_scan_summary(code_source))

    with console.status("Scanning for functionalities..."):
        disc_result = _discover.discover(code_source)

    funcs = disc_result.functionalities
    if functionality_name:
        funcs = [f for f in funcs if f.name == functionality_name]

    all_discoveries = []
    for func in funcs:
        with console.status(f"Discovering containers for: {func.name}..."):
            func_tier, _, _ = _assess.assess(func, code_source, None)
            disc = _container.discover(code_source, func.name, func_tier)
        all_discoveries.append((func, func_tier, disc))

    if output_json:
        output = []
        for func, func_tier, disc in all_discoveries:
            default_opt = next((o for o in disc.options if o.is_default), disc.options[0])
            output.append({
                "functionality": func.name,
                "tier": func_tier,
                "default": default_opt.source.value,
                "options": [
                    {
                        "source": o.source.value,
                        "docker_url": o.docker_url,
                        "singularity_url": o.singularity_url,
                        "is_default": o.is_default,
                        "warnings": o.warnings,
                    }
                    for o in disc.options
                ],
            })
        click.echo(json.dumps(output, indent=2))
        return

    for func, func_tier, disc in all_discoveries:
        _container._print_menu(disc, console)
        console.print("\nTo use a specific option:")
        console.print("  code-to-module convert SOURCE --container biocontainers")
        console.print("  code-to-module convert SOURCE --container dockerfile")
        console.print("  code-to-module convert SOURCE --container stub")


# ── bioconda-recipe ────────────────────────────────────────────────────────────


@main.command("bioconda-recipe")
@click.argument("source")
@click.option("--tool-name", default=None, help="Override detected tool name.")
@click.option(
    "--outdir",
    default=".",
    type=click.Path(),
    show_default=True,
    help="Directory to write meta.yaml.",
)
def bioconda_recipe(source: str, tool_name: str | None, outdir: str) -> None:
    """Generate a Bioconda recipe scaffold for a tool not yet in Bioconda."""
    from rich.panel import Panel

    from code_to_module.bioconda import check_bioconda, generate_recipe
    from code_to_module.ingest import ingest

    console = _make_console()

    code_source = ingest(source)
    resolved_name = tool_name or Path(code_source.filename).stem

    status = check_bioconda(resolved_name)

    if status.exists:
        console.print(
            Panel(
                f"[green]✓ {resolved_name} is already in Bioconda![/green]\n"
                f"  Latest version: {status.latest_version}\n"
                f"  BioContainers: {status.biocontainers_url}\n"
                f"  Re-run convert with --container biocontainers to use this image.",
                border_style="green",
            )
        )
        sys.exit(0)

    console.print(
        Panel(
            f"[yellow]⚠ {resolved_name} is not in Bioconda.[/yellow]\n"
            "  Generating meta.yaml scaffold...",
            border_style="yellow",
        )
    )

    recipe = generate_recipe(code_source, resolved_name)
    if recipe is None:
        console.print("[red]Failed to generate recipe.[/red]")
        sys.exit(1)

    out_path = Path(outdir)
    out_path.mkdir(parents=True, exist_ok=True)
    meta_file = out_path / "meta.yaml"
    meta_file.write_text(recipe.meta_yaml_content, encoding="utf-8")

    for warning in recipe.warnings:
        console.print(f"[yellow]⚠ {warning}[/yellow]")

    console.print(
        Panel(
            f"Bioconda recipe generated: {meta_file}\n\n"
            "Next steps:\n"
            "1. Review meta.yaml and fill in all TODO fields\n"
            f"2. Compute sha256: sha256sum {recipe.version}.tar.gz\n"
            "3. Fork https://github.com/bioconda/bioconda-recipes\n"
            f"4. Add your recipe under recipes/{resolved_name}/meta.yaml\n"
            "5. Open a PR — the Bioconda bot will test and merge it\n"
            "6. Once merged, BioContainers auto-builds the Docker image (usually 24-48h)\n"
            f"7. Re-run: code-to-module convert {source} --container biocontainers\n\n"
            "While waiting for Bioconda merge, run convert normally — a Dockerfile\n"
            "will be generated as a temporary container.",
            border_style="blue",
        )
    )


# ── update-standards ───────────────────────────────────────────────────────────


@main.command("update-standards")
def update_standards() -> None:
    """Update bundled nf-core standards to the latest version."""
    from importlib.resources import files

    from code_to_module.standards import Standards, get_standards

    console = _make_console()

    current = get_standards()
    console.print(f"Current schema version: {current.schema_version}")
    console.print("Checking for updates...")

    newer = current.check_for_updates()
    if newer is None:
        console.print("[green]Standards are up to date.[/green]")
        return

    console.print(f"[yellow]New version available: {newer}[/yellow]")
    console.print("Downloading and saving...")

    data_dir = Path(str(files("code_to_module.standards"))) / "data"
    schema_path = data_dir / "nf_core_standards.json"

    try:
        updated = Standards.fetch_and_save(schema_path)
        console.print(
            f"[green]Standards updated to {updated.schema_version}[/green]\n"
            f"Saved to {schema_path}"
        )
    except Exception as exc:
        console.print(f"[red]Update failed: {exc}[/red]")
        raise SystemExit(1)
