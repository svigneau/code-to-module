"""CLI entry point for ctm-validate — module validation suite.

Commands: test, fix, review.

IMPORTANT: This file must NOT import from ingest, discover, assess, infer,
container, or generate. It may only import from validate, fix, review,
standards, and models. This boundary enables future extraction of the
validation suite into a standalone package.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from code_to_module.fix import ProposedFix, apply_approved_fixes, propose_fixes
from code_to_module.review import review_module
from code_to_module.standards import get_standards
from code_to_module.validate import TestReport, run_validation


@click.group(name="validate-module")
def main() -> None:
    """Validate, fix, and review nf-core modules."""


# ── ctm-validate test ──────────────────────────────────────────────────────────


@main.command("test")
@click.argument("module_path", metavar="MODULE_PATH", type=click.Path(exists=True))
@click.option("--lint-only", is_flag=True, default=False, help="Run nf-core lint only.")
@click.option("--nftest-only", is_flag=True, default=False, help="Run nf-test only.")
@click.option(
    "--json-output",
    type=click.Path(),
    default=None,
    metavar="PATH",
    help="Write TestReport as JSON to PATH.",
)
def test_cmd(
    module_path: str,
    lint_only: bool,
    nftest_only: bool,
    json_output: str | None,
) -> None:
    """Run nf-core lint and nf-test against the module at MODULE_PATH."""
    console = Console()
    standards = get_standards()
    report = run_validation(
        Path(module_path), standards, lint_only=lint_only, nftest_only=nftest_only, console=console
    )

    if json_output is not None:
        out_path = Path(json_output)
        out_path.write_text(report.model_dump_json(indent=2))
        console.print(f"[dim]Report written to {out_path}[/dim]")

    sys.exit(0 if (report.lint_passed and report.nftest_passed) else 1)


# ── ctm-validate fix ───────────────────────────────────────────────────────────


def _fix_panel(
    fix_index: int,
    total: int,
    fix: ProposedFix,
    console: Console,
) -> None:
    """Render a Rich panel showing one ProposedFix."""
    from code_to_module.fix import FixSource  # local to avoid circular at module level

    source_tag = "[rule]" if fix.fix_source == FixSource.RULE else "[LLM]"
    title = f"Fix {fix_index} of {total}  {source_tag}  {fix.description}"
    file_display = f"File: {Path(fix.file_path).name}"
    if fix.line_start:
        file_display += f"  line {fix.line_start}"

    lines: list[str] = [file_display, ""]

    if fix.is_deletion:
        lines.append(f"[yellow]⚠ Will delete: {Path(fix.file_path).name}[/yellow]")
    else:
        lines.extend(fix.diff.splitlines())

    if fix.fix_source == FixSource.LLM:
        lines.insert(0, "[yellow]⚡ LLM-proposed fix — review carefully before approving[/yellow]")
        lines.insert(1, "")

    body = "\n".join(lines)
    border = "cyan" if fix.fix_source == FixSource.RULE else "yellow"
    console.print(Panel(body, title=title, border_style=border))


def _prompt_fix(fix: ProposedFix, current_class: str, console: Console) -> str:
    """Show the approval prompt for a fix. Returns the user's choice (y/n/a/s/q)."""
    prompt_text = (
        f"Apply this fix? "
        f"[Y=yes, n=skip, a=approve all {current_class}, s=skip all {current_class}, q=quit]"
    )
    try:
        answer = click.prompt(prompt_text, default="y", show_default=False).strip().lower()
    except (click.Abort, EOFError):
        answer = "q"
    return answer if answer in ("y", "n", "a", "s", "q", "") else "y"


def _print_class_c_panels(report: TestReport, console: Console) -> None:
    """Show Class C failures as informational panels (no fix offered)."""
    from code_to_module.validate import FixClass  # local to avoid circular at module level

    class_c_lint = [f for f in report.lint_failures if f.fix_class == FixClass.CLASS_C]
    class_c_nft = [f for f in report.nftest_failures if f.fix_class == FixClass.CLASS_C]

    for failure in class_c_lint + class_c_nft:
        if hasattr(failure, "code"):
            title = f"CLASS C: {failure.code}"
            body = f"{failure.message}\n\nThis cannot be fixed automatically.\n{failure.fix_hint}"  # type: ignore[union-attr]
        else:
            title = f"CLASS C: {failure.test_name}"
            body = f"{failure.error[:300]}\n\nThis cannot be fixed automatically.\n{failure.fix_hint}"
        console.print(Panel(body, title=title, border_style="red"))


@main.command("fix")
@click.argument("module_path", metavar="MODULE_PATH", type=click.Path(exists=True))
@click.option(
    "--from-report",
    type=click.Path(),
    default=None,
    help="Load existing TestReport JSON instead of re-running validation.",
)
@click.option(
    "--class-a-only",
    is_flag=True,
    default=False,
    help="Only propose rule-based (Class A) fixes.",
)
@click.option(
    "--yes", "-y",
    is_flag=True,
    default=False,
    help="Auto-approve all Class A fixes (never auto-approves Class B).",
)
@click.option(
    "--no-revalidate",
    is_flag=True,
    default=False,
    help="Skip re-validation after applying fixes.",
)
def fix_cmd(
    module_path: str,
    from_report: str | None,
    class_a_only: bool,
    yes: bool,
    no_revalidate: bool,
) -> None:
    """Propose and apply fixes for lint and nf-test failures."""
    from code_to_module.validate import FixClass, TestReport  # local to avoid circular

    console = Console()
    standards = get_standards()
    path = Path(module_path)

    # ── Step 1: Obtain report ──────────────────────────────────────────────────
    if from_report is not None:
        report = TestReport.model_validate_json(Path(from_report).read_text())
    else:
        console.print("[bold]Running validation...[/bold]")
        report = run_validation(path, standards, console=console)

    total_failures = len(report.lint_failures) + len(report.nftest_failures)
    if total_failures == 0:
        console.print("[green]✓ No failures found — nothing to fix.[/green]")
        return

    console.print(
        f"[bold red]✗[/bold red] {total_failures} failure"
        f"{'s' if total_failures != 1 else ''}: "
        f"{report.class_a_count} Class A, "
        f"{report.class_b_count} Class B, "
        f"{report.class_c_count} Class C"
    )

    # ── Step 2: Propose fixes ──────────────────────────────────────────────────
    all_fixes = propose_fixes(report, standards)
    if class_a_only:
        all_fixes = [f for f in all_fixes if f.fix_class == FixClass.CLASS_A]

    if not all_fixes:
        console.print("[yellow]No automatic fixes available for these failures.[/yellow]")
        _print_class_c_panels(report, console)
        return

    total = len(all_fixes)

    # ── Step 3: Present fixes and collect approvals ───────────────────────────
    skip_class_a = False
    skip_class_b = False
    quit_mode = False

    for idx, fix in enumerate(all_fixes, start=1):
        if quit_mode:
            break

        current_class = "Class A" if fix.fix_class == FixClass.CLASS_A else "Class B"

        # --yes auto-approves CLASS_A without prompting
        if yes and fix.fix_class == FixClass.CLASS_A:
            fix.approved = True
            console.print(f"[green]✓ Fix {idx} approved (auto): {fix.description}[/green]")
            continue

        if fix.fix_class == FixClass.CLASS_A and skip_class_a:
            fix.approved = False
            continue
        if fix.fix_class == FixClass.CLASS_B and skip_class_b:
            fix.approved = False
            continue

        if yes and fix.fix_class == FixClass.CLASS_B:
            console.print(
                "[yellow]⚡ LLM-proposed fix — --yes does not auto-approve LLM fixes[/yellow]"
            )

        _fix_panel(idx, total, fix, console)
        answer = _prompt_fix(fix, current_class, console)

        if answer in ("y", ""):
            fix.approved = True
            console.print(f"[green]✓ Fix {idx} approved[/green]")
        elif answer == "n":
            fix.approved = False
        elif answer == "a":
            # Approve this one and all remaining of same class
            fix.approved = True
            console.print(f"→ Approving all remaining {current_class} fixes automatically.")
            console.print(f"[green]✓ Fix {idx} approved[/green]")
            if fix.fix_class == FixClass.CLASS_A:
                for later in all_fixes[idx:]:
                    if later.fix_class == FixClass.CLASS_A:
                        later.approved = True
                skip_class_a = True
            else:
                for later in all_fixes[idx:]:
                    if later.fix_class == FixClass.CLASS_B:
                        later.approved = True
                skip_class_b = True
        elif answer == "s":
            fix.approved = False
            if fix.fix_class == FixClass.CLASS_A:
                skip_class_a = True
            else:
                skip_class_b = True
        elif answer == "q":
            fix.approved = False
            quit_mode = True

    # ── Step 4: Apply approved fixes ──────────────────────────────────────────
    applied_count = apply_approved_fixes(all_fixes)

    # Report invalidations
    for idx, fix in enumerate(all_fixes, start=1):
        if fix.invalidated:
            console.print(
                f"[yellow]⚠ Fix {idx} skipped — {fix.invalidation_reason}[/yellow]"
            )

    console.print(f"\nApplied {applied_count} fix{'es' if applied_count != 1 else ''}.", end="")

    # ── Step 5: Re-validate ────────────────────────────────────────────────────
    if no_revalidate or applied_count == 0:
        console.print()
        _print_class_c_panels(report, console)
        return

    console.print(" Re-running validation...\n")
    new_report = run_validation(path, standards, console=console)

    remaining = len(new_report.lint_failures) + len(new_report.nftest_failures)
    if remaining == 0:
        console.print("[green]✓ All fixable failures resolved[/green]")
    else:
        console.print(
            f"[yellow]✗ {remaining} failure"
            f"{'s' if remaining != 1 else ''} remain.[/yellow]"
        )

    # Always show Class C panels at the end
    _print_class_c_panels(new_report, console)
    sys.exit(0 if remaining == 0 else 1)


# ── ctm-validate review ────────────────────────────────────────────────────────


@main.command("review")
@click.argument("module_path", metavar="MODULE_PATH", type=click.Path(exists=True))
@click.option("--errors-only", is_flag=True, default=False, help="Show only ERROR severity items.")
@click.option(
    "--json-output",
    type=click.Path(),
    default=None,
    metavar="PATH",
    help="Write ReviewReport as JSON to PATH.",
)
def review_cmd(module_path: str, errors_only: bool, json_output: str | None) -> None:
    """Static analysis of a module against nf-core style conventions."""
    console = Console()
    standards = get_standards()
    report = review_module(
        Path(module_path), standards, console=console, errors_only=errors_only
    )

    if json_output is not None:
        out_path = Path(json_output)
        out_path.write_text(report.model_dump_json(indent=2))
        console.print(f"[dim]Report written to {out_path}[/dim]")

    sys.exit(0 if report.submission_ready else 1)
