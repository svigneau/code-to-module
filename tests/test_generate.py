"""Tests for generate.py."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from rich.console import Console

from code_to_module.generate import generate
from code_to_module.models import (
    ChannelSpec,
    ContainerSource,
    ExistingModule,
    ModuleSpec,
    TestCase,
    TestDataSource,
    TestSpec,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def make_test_spec(tool_name: str = "testtool", version_command: str = "") -> ModuleSpec:
    return ModuleSpec(
        tool_name=tool_name,
        process_name=tool_name.upper() + "_RUN",
        functionality_name="run",
        inputs=[ChannelSpec(name="bam", type="map", description="Input BAM file")],
        outputs=[ChannelSpec(name="sorted_bam", type="map", description="Sorted BAM")],
        container_docker=f"quay.io/biocontainers/{tool_name}:1.0.0--pyhdfd78af_0",
        container_singularity=f"https://depot.galaxyproject.org/singularity/{tool_name}:1.0.0--pyhdfd78af_0",
        container_source=ContainerSource.BIOCONTAINERS,
        dockerfile_content=None,
        label="process_medium",
        ext_args="",
        tier=1,
        confidence=0.90,
        version_command=version_command,
    )


def render_module(spec: ModuleSpec) -> str:
    """Generate module files and return the main.nf content."""
    console, _ = _capture_console()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        generate(spec, _minimal_test_spec(), tmp_path, console=console)
        return (tmp_path / spec.tool_name / "main.nf").read_text()


def _minimal_spec(
    container_source: ContainerSource = ContainerSource.BIOCONTAINERS,
    dockerfile_content: str | None = None,
) -> ModuleSpec:
    return ModuleSpec(
        tool_name="mytool",
        process_name="MYTOOL_RUN",
        functionality_name="run",
        inputs=[ChannelSpec(name="bam", type="map", description="Input BAM file")],
        outputs=[ChannelSpec(name="sorted_bam", type="map", description="Sorted BAM")],
        container_docker="quay.io/biocontainers/mytool:1.0.0--pyhdfd78af_0",
        container_singularity="https://depot.galaxyproject.org/singularity/mytool:1.0.0--pyhdfd78af_0",
        container_source=container_source,
        dockerfile_content=dockerfile_content,
        label="process_medium",
        ext_args="",
        tier=1,
        confidence=0.90,
    )


def _minimal_test_spec() -> TestSpec:
    return TestSpec(
        process_name="MYTOOL_RUN",
        test_cases=[
            TestCase(
                name="test_basic",
                input_files=["genomics/sarscov2/illumina/bam/test.paired_end.bam"],
                input_sources=[TestDataSource.NFCORE_DATASETS],
                expected_outputs=["sorted_bam"],
            )
        ],
    )


def _capture_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, no_color=True, highlight=False)
    return console, buf


def _existing_module(
    container_docker: str = "quay.io/biocontainers/mytool:1.0.0--pyhdfd78af_0",
    label: str = "process_medium",
    load_error: str | None = None,
) -> ExistingModule:
    return ExistingModule(
        path=Path("/fake/module/path"),
        tool_name="mytool",
        process_name="MYTOOL_OTHER",
        container_docker=container_docker,
        container_singularity="https://depot.galaxyproject.org/singularity/mytool:1.0.0",
        label=label,
        load_error=load_error,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_generate_creates_files(tmp_path: Path) -> None:
    """All 5 module files are written when dockerfile_content is set."""
    spec = _minimal_spec(
        container_source=ContainerSource.BIOCONTAINERS,
        dockerfile_content="FROM ubuntu:22.04\n",
    )
    test_spec = _minimal_test_spec()
    console, _ = _capture_console()

    created = generate(spec, test_spec, tmp_path, console=console)

    tool_dir = tmp_path / "mytool"
    assert (tool_dir / "main.nf").is_file()
    assert (tool_dir / "meta.yml").is_file()
    assert (tool_dir / "environment.yml").is_file()
    assert (tool_dir / "Dockerfile").is_file()
    assert (tool_dir / "tests" / "main.nf.test").is_file()
    assert len(created) == 5


def test_generate_no_dockerfile_when_content_none(tmp_path: Path) -> None:
    """No Dockerfile written when dockerfile_content is None."""
    spec = _minimal_spec(dockerfile_content=None)
    console, _ = _capture_console()

    created = generate(spec, _minimal_test_spec(), tmp_path, console=console)

    tool_dir = tmp_path / "mytool"
    assert not (tool_dir / "Dockerfile").exists()
    assert len(created) == 4


def test_generate_derivation_script(tmp_path: Path) -> None:
    """derive_test_data.sh and tests/data/.gitkeep are written when needs_derivation=True."""
    spec = _minimal_spec()
    test_spec = TestSpec(
        process_name="MYTOOL_RUN",
        test_cases=[],
        needs_derivation=True,
        derivation_script_content=(
            "#!/usr/bin/env bash\n"
            "# IMPORTANT: After running this script, the generated files should be submitted\n"
            "# to nf-core/test-datasets via a pull request. See:\n"
            "# https://nf-co.re/docs/tutorials/tests_and_test_data/test_data#adding-new-test-data\n"
            "echo done\n"
        ),
    )
    console, _ = _capture_console()

    created = generate(spec, test_spec, tmp_path, console=console)

    script = tmp_path / "mytool" / "tests" / "derive_test_data.sh"
    assert script.is_file()
    assert script.stat().st_mode & 0o111  # executable
    assert (tmp_path / "mytool" / "tests" / "data" / ".gitkeep").is_file()
    assert script in created


def test_no_duplicate_versions_emit(tmp_path: Path) -> None:
    """Bug 1: LLM-returned 'versions' output channel must not produce a second emit."""
    spec = _minimal_spec()
    # Simulate the LLM including a versions channel in its output list
    spec = spec.model_copy(update={
        "outputs": [
            ChannelSpec(name="sorted_bam", type="map", description="Sorted BAM"),
            ChannelSpec(name="versions", type="val", description="Software versions"),
        ]
    })
    console, _ = _capture_console()
    generate(spec, _minimal_test_spec(), tmp_path, console=console)

    content = (tmp_path / "mytool" / "main.nf").read_text()
    # Only the topic-channel form should appear; the bare val(versions) must not
    assert content.count("emit: versions") == 1
    assert "topic: versions" in content
    assert "val(versions), emit: versions" not in content


def test_script_template_rendered(tmp_path: Path) -> None:
    """Bug 2: spec.script_template is rendered in the script block instead of TODO."""
    spec = _minimal_spec()
    spec = spec.model_copy(update={
        "script_template": "mytool -i $bam -o ${prefix}.sorted.bam $args"
    })
    console, _ = _capture_console()
    generate(spec, _minimal_test_spec(), tmp_path, console=console)

    content = (tmp_path / "mytool" / "main.nf").read_text()
    # Command has 3 arg groups → multi-line; check all parts present
    assert "-i $bam" in content
    assert "-o ${prefix}.sorted.bam" in content
    assert "// TODO: add actual" not in content


def test_script_template_empty_shows_todo(tmp_path: Path) -> None:
    """Bug 2: empty script_template falls back to TODO comment."""
    spec = _minimal_spec()
    # script_template defaults to ""
    console, _ = _capture_console()
    generate(spec, _minimal_test_spec(), tmp_path, console=console)

    content = (tmp_path / "mytool" / "main.nf").read_text()
    assert "// TODO: add actual mytool command here" in content


def test_optional_input_channel(tmp_path: Path) -> None:
    """Bug 3: optional input channel rendered with 'optional: true' modifier."""
    spec = _minimal_spec()
    spec = spec.model_copy(update={
        "inputs": [
            ChannelSpec(name="bam", type="map", description="Input BAM", optional=False),
            ChannelSpec(name="gene_file", type="file", description="Gene list", optional=True),
        ]
    })
    console, _ = _capture_console()
    generate(spec, _minimal_test_spec(), tmp_path, console=console)

    content = (tmp_path / "mytool" / "main.nf").read_text()
    assert "path(gene_file, optional: true)" in content
    # Required channel must NOT have optional modifier
    assert "path(bam)" in content or "path(bam," not in content


def test_versions_not_in_stub_touch(tmp_path: Path) -> None:
    """Bug 1: stub block must not try to touch a 'versions' file."""
    spec = _minimal_spec()
    spec = spec.model_copy(update={
        "outputs": [
            ChannelSpec(name="sorted_bam", type="map", description="Sorted BAM"),
            ChannelSpec(name="versions", type="val", description="Software versions"),
        ]
    })
    console, _ = _capture_console()
    generate(spec, _minimal_test_spec(), tmp_path, console=console)

    content = (tmp_path / "mytool" / "main.nf").read_text()
    # The stub block must not touch a versions file
    stub_block = content.split("stub:")[1] if "stub:" in content else ""
    assert "touch ${prefix}.versions" not in stub_block


def test_stub_uses_glob_pattern_filename(tmp_path: Path) -> None:
    """Bug 1: stub touch uses the glob pattern suffix, not the emit name."""
    spec = _minimal_spec()
    spec = spec.model_copy(update={
        "outputs": [
            ChannelSpec(
                name="predictions",
                type="map",
                description="Predictions CSV",
                pattern="*predictions.csv",
            ),
        ]
    })
    console, _ = _capture_console()
    generate(spec, _minimal_test_spec(), tmp_path, console=console)

    content = (tmp_path / "mytool" / "main.nf").read_text()
    stub_block = content.split("stub:")[1] if "stub:" in content else ""
    assert "touch ${prefix}predictions.csv" in stub_block
    # Must NOT touch the emit name
    assert "touch ${prefix}.predictions" not in stub_block


def test_stub_brace_expansion(tmp_path: Path) -> None:
    """Bug 1: brace glob *.{png,pdf} expands to multiple touch commands."""
    spec = _minimal_spec()
    spec = spec.model_copy(update={
        "outputs": [
            ChannelSpec(
                name="plots",
                type="file",
                description="Plot files",
                pattern="*.{png,pdf}",
            ),
        ]
    })
    console, _ = _capture_console()
    generate(spec, _minimal_test_spec(), tmp_path, console=console)

    content = (tmp_path / "mytool" / "main.nf").read_text()
    stub_block = content.split("stub:")[1] if "stub:" in content else ""
    assert "touch ${prefix}.png" in stub_block
    assert "touch ${prefix}.pdf" in stub_block


def test_stub_val_channel_not_touched(tmp_path: Path) -> None:
    """val-type output channels are not touched in the stub block."""
    spec = _minimal_spec()
    spec = spec.model_copy(update={
        "outputs": [
            ChannelSpec(name="count", type="val", description="Count"),
        ]
    })
    console, _ = _capture_console()
    generate(spec, _minimal_test_spec(), tmp_path, console=console)

    content = (tmp_path / "mytool" / "main.nf").read_text()
    stub_block = content.split("stub:")[1] if "stub:" in content else ""
    assert "touch" not in stub_block


def test_script_multiline_format(tmp_path: Path) -> None:
    """Bug 2: script template with >2 args is broken across lines with backslash."""
    spec = _minimal_spec()
    spec = spec.model_copy(update={
        "script_template": "celltypist -i $indata -m $model -o $outdir $args"
    })
    console, _ = _capture_console()
    generate(spec, _minimal_test_spec(), tmp_path, console=console)

    content = (tmp_path / "mytool" / "main.nf").read_text()
    # Should contain backslash continuation
    assert "\\\n" in content
    # Each flag-value pair should appear
    assert "-i $indata" in content
    assert "-m $model" in content
    assert "-o $outdir" in content


def test_script_short_command_stays_single_line(tmp_path: Path) -> None:
    """Script template with <=2 args stays on one line (no backslash)."""
    spec = _minimal_spec()
    spec = spec.model_copy(update={
        "script_template": "mytool -i $bam $args"
    })
    console, _ = _capture_console()
    generate(spec, _minimal_test_spec(), tmp_path, console=console)

    content = (tmp_path / "mytool" / "main.nf").read_text()
    assert "mytool -i $bam $args" in content


def test_consistency_check_container_divergence(tmp_path: Path) -> None:
    """Diverging container URL → warning printed; files still written; no exception."""
    spec = _minimal_spec()
    existing = [_existing_module(container_docker="quay.io/biocontainers/mytool:0.9.0")]
    console, buf = _capture_console()

    created = generate(spec, _minimal_test_spec(), tmp_path,
                       existing_modules=existing, console=console)

    output = buf.getvalue()
    assert "Consistency warning" in output
    assert "container" in output.lower()
    assert (tmp_path / "mytool" / "main.nf").is_file()
    assert len(created) >= 4


def test_consistency_check_label_divergence(tmp_path: Path) -> None:
    """Diverging process label → warning printed."""
    spec = _minimal_spec()
    existing = [_existing_module(label="process_high")]
    console, buf = _capture_console()

    generate(spec, _minimal_test_spec(), tmp_path,
             existing_modules=existing, console=console)

    output = buf.getvalue()
    assert "Consistency warning" in output
    assert "label" in output.lower()


def test_consistency_check_matching(tmp_path: Path) -> None:
    """Matching container and label → no warning printed."""
    spec = _minimal_spec()
    existing = [_existing_module(
        container_docker=spec.container_docker,
        label=spec.label,
    )]
    console, buf = _capture_console()

    generate(spec, _minimal_test_spec(), tmp_path,
             existing_modules=existing, console=console)

    assert "Consistency warning" not in buf.getvalue()


def test_consistency_check_skipped_when_empty(tmp_path: Path) -> None:
    """Empty existing_modules list → no check runs."""
    spec = _minimal_spec()
    console, buf = _capture_console()

    generate(spec, _minimal_test_spec(), tmp_path,
             existing_modules=[], console=console)

    assert "Consistency warning" not in buf.getvalue()


def test_generated_main_nf_uses_eval_versions(tmp_path: Path) -> None:
    """Generated main.nf uses eval() for version capture, not env(TOOL_VERSION)."""
    spec = make_test_spec(tool_name="testtool", version_command="testtool --version")
    result = render_module(spec)
    assert 'eval("testtool --version' in result
    assert 'env(TOOL_VERSION)' not in result
    assert 'TOOL_VERSION=' not in result


def test_generated_stub_has_no_tool_version_assignment(tmp_path: Path) -> None:
    """Stub block does not contain TOOL_VERSION= after eval migration."""
    spec = make_test_spec(tool_name="testtool", version_command="testtool --version")
    result = render_module(spec)
    stub_section = result.split('stub:')[1] if 'stub:' in result else ''
    assert 'TOOL_VERSION=' not in stub_section


def test_eval_emit_name_uses_tool_name(tmp_path: Path) -> None:
    """versions emit name is versions_{tool_name}, not generic 'versions'."""
    spec = make_test_spec(tool_name="celltypist", version_command="celltypist --version 2>&1 | head -1")
    result = render_module(spec)
    assert 'emit: versions_celltypist' in result


def test_consistency_check_load_error_skipped(tmp_path: Path) -> None:
    """ExistingModule with load_error → skipped; no crash; no spurious warning."""
    spec = _minimal_spec()
    existing = [_existing_module(
        container_docker="quay.io/biocontainers/mytool:0.9.0",  # would diverge
        label="process_high",                                     # would diverge
        load_error="Failed to parse main.nf",
    )]
    console, buf = _capture_console()

    generate(spec, _minimal_test_spec(), tmp_path,
             existing_modules=existing, console=console)

    # load_error set → skipped → no warning
    assert "Consistency warning" not in buf.getvalue()
