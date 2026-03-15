"""End-to-end integration tests.

Real file generation; only infer_module_spec_sync and httpx.get are mocked.
All other pipeline stages (ingest, discover, assess, container, test_gen,
generate) run against real code.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from code_to_module.cli import main
from code_to_module.models import ChannelSpec, ContainerSource, ModuleSpec
from code_to_module.standards import get_standards

# ── Fixtures / shared helpers ──────────────────────────────────────────────────


def _make_infer_spec(func_name: str, tool_name: str) -> ModuleSpec:
    """Minimal ModuleSpec that the infer mock returns.

    Container fields are intentionally empty — the CLI overwrites them
    from the resolved ContainerOption before calling generate().
    """
    return ModuleSpec(
        tool_name=tool_name,
        process_name=tool_name.upper().replace("-", "_"),
        functionality_name=func_name,
        inputs=[
            ChannelSpec(name="input", type="file", description="Input file"),
        ],
        outputs=[
            ChannelSpec(
                name="output",
                type="file",
                description="Output file",
                pattern="*.out",
            ),
        ],
        container_docker="",        # overwritten by CLI
        container_singularity="",   # overwritten by CLI
        container_source=ContainerSource.STUB,
        label="process_single",
        ext_args="def args = task.ext.args ?: ''",
        tier=3,
        confidence=0.9,
    )


def _infer_side_effect(func, source, tier):  # noqa: ANN001
    """infer_module_spec_sync side_effect: map func.name → a minimal spec."""
    return _make_infer_spec(func.name, func.name)


def _make_httpx_mock(
    bioconda_200: list[str] = (),
    biotools_200: list[str] = (),
    bioconda_version: str = "1.19",
):
    """Return a side_effect callable for httpx.get.

    Any tool name in *bioconda_200* returns HTTP 200 with ``latest_version``.
    All other Bioconda / bio.tools lookups return 404.
    GitHub tags API always returns a fake v1.0.0 tag.
    """
    bioconda_set = set(bioconda_200)
    biotools_set = set(biotools_200)

    def _mock_get(url: str, **kwargs) -> MagicMock:
        resp = MagicMock()
        if "api.anaconda.org/package/bioconda/" in url:
            tool = url.rstrip("/").split("/")[-1]
            if tool in bioconda_set:
                resp.status_code = 200
                resp.json.return_value = {"latest_version": bioconda_version}
            else:
                resp.status_code = 404
        elif "bio.tools/api/tool/" in url:
            tool = url.rstrip("/").split("/")[-1]
            resp.status_code = 200 if tool in biotools_set else 404
        elif "api.github.com/repos" in url:
            resp.status_code = 200
            resp.json.return_value = [{"name": "v1.0.0"}]
        else:
            resp.status_code = 404
        return resp

    return _mock_get


# ── Source file templates ──────────────────────────────────────────────────────

_SAMTOOLS_ARGPARSE = """\
#!/usr/bin/env python
\"\"\"Sort BAM files using samtools.\"\"\"
import argparse
import subprocess

parser = argparse.ArgumentParser(description="Sort BAM")
parser.add_argument("bam", help="Input BAM file")
parser.add_argument("--output", "-o", required=True, help="Output file")
args = parser.parse_args()

subprocess.run(["samtools", "sort", "-o", args.output, args.bam])
"""

_PIPELINE_SCRIPT = """\
#!/usr/bin/env python
\"\"\"Run my custom pipeline.\"\"\"
import subprocess

output_file = "result.txt"
subprocess.run(["my_pipeline_tool", "--out", output_file])
"""

_CLICK_THREE_COMMANDS = """\
import click
import subprocess


@click.group()
def cli():
    pass


@cli.command()
@click.argument("bam")
@click.option("--output", "-o", required=True)
def sort(bam, output):
    \"\"\"Sort a BAM file using samtools sort.\"\"\"
    input_path = bam
    output_path = output
    cmd = ["samtools", "sort", "-o", output_path, input_path]
    subprocess.run(cmd, check=True)
    return output_path


@cli.command()
@click.argument("bam")
@click.option("--output", "-o", required=True)
def index(bam, output):
    \"\"\"Index a BAM file using samtools index.\"\"\"
    input_path = bam
    output_path = bam + ".bai"
    cmd = ["samtools", "index", input_path]
    subprocess.run(cmd, check=True)
    return output_path


@cli.command()
@click.argument("reads")
@click.argument("ref")
@click.option("--output", "-o", required=True)
def align(reads, ref, output):
    \"\"\"Align reads to reference using samtools mem.\"\"\"
    input_reads = reads
    input_ref = ref
    output_path = output
    cmd = ["samtools", "mem", input_ref, input_reads]
    subprocess.run(cmd, check=True)
    return output_path


if __name__ == "__main__":
    cli()
"""

_CLICK_TWO_MIXED_TIER = """\
import click
import subprocess


@click.group()
def cli():
    pass


@cli.command()
@click.argument("bam")
@click.option("--output", "-o", required=True)
def sort(bam, output):
    \"\"\"Sort a BAM file using samtools sort.\"\"\"
    input_path = bam
    output_path = output
    cmd = ["samtools", "sort", "-o", output_path, input_path]
    subprocess.run(cmd, check=True)
    return output_path


@cli.command()
def index():
    pass


if __name__ == "__main__":
    cli()
"""


# ── Scenario A: single script, BioContainers available ────────────────────────


def test_e2e_single_bioconda(tmp_path: Path) -> None:
    """Scenario A: single samtools.py → BioContainers selected → quay.io URL."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "samtools.py").write_text(_SAMTOOLS_ARGPARSE)

    s = get_standards()
    expected_docker = f"{s.docker_registry}samtools:1.19--pyhdfd78af_0"
    outdir = tmp_path / "out"

    runner = CliRunner()
    with (
        patch("httpx.get", side_effect=_make_httpx_mock(bioconda_200=["samtools"])),
        patch(
            "code_to_module.api.infer_module_spec_sync",
            side_effect=_infer_side_effect,
        ),
    ):
        result = runner.invoke(
            main,
            [
                "convert",
                str(repo / "samtools.py"),
                "--outdir",
                str(outdir),
                "--no-interaction",
                "--no-update-check",
            ],
        )

    assert result.exit_code == 0, result.output
    main_nf = outdir / "samtools" / "main.nf"
    assert main_nf.exists(), "main.nf was not generated"
    content = main_nf.read_text()
    assert expected_docker in content, (
        f"Expected '{expected_docker}' in main.nf\nActual:\n{content}"
    )
    meta_yml = outdir / "samtools" / "meta.yml"
    assert meta_yml.exists(), "meta.yml was not generated"
    yaml.safe_load(meta_yml.read_text())  # Must parse as valid YAML


# ── Scenario B: repo directory with Dockerfile ────────────────────────────────


def test_e2e_repo_with_dockerfile(tmp_path: Path) -> None:
    """Scenario B: dir with script + Dockerfile → DOCKERFILE selected → :latest URL."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pipeline.py").write_text(_PIPELINE_SCRIPT)
    (repo / "Dockerfile").write_text("FROM ubuntu:22.04\nRUN apt-get update\n")

    outdir = tmp_path / "out"

    runner = CliRunner()
    with (
        patch("httpx.get", side_effect=_make_httpx_mock()),  # all 404
        patch(
            "code_to_module.api.infer_module_spec_sync",
            side_effect=_infer_side_effect,
        ),
    ):
        result = runner.invoke(
            main,
            [
                "convert",
                str(repo),
                "--outdir",
                str(outdir),
                "--no-interaction",
                "--no-update-check",
            ],
        )

    assert result.exit_code == 0, result.output
    # The primary script is pipeline.py → func.name = "pipeline"
    main_nf = outdir / "pipeline" / "main.nf"
    assert main_nf.exists(), "main.nf was not generated"
    content = main_nf.read_text()
    # DOCKERFILE container source: template emits 'docker://pipeline:latest' for singularity
    assert "pipeline:latest" in content, (
        f"Expected 'pipeline:latest' in main.nf\nActual:\n{content}"
    )
    meta_yml = outdir / "pipeline" / "meta.yml"
    assert meta_yml.exists(), "meta.yml was not generated"
    yaml.safe_load(meta_yml.read_text())


# ── Scenario E: no container, Bioconda 404, STUB ──────────────────────────────


def test_e2e_stub_no_container(tmp_path: Path) -> None:
    """Scenario E: no Dockerfile, Bioconda 404 → STUB with TODO in main.nf."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "samtools.py").write_text(_SAMTOOLS_ARGPARSE)

    outdir = tmp_path / "out"

    runner = CliRunner()
    with (
        patch("httpx.get", side_effect=_make_httpx_mock()),  # all 404
        patch(
            "code_to_module.api.infer_module_spec_sync",
            side_effect=_infer_side_effect,
        ),
    ):
        result = runner.invoke(
            main,
            [
                "convert",
                str(repo / "samtools.py"),
                "--outdir",
                str(outdir),
                "--no-interaction",
                "--no-update-check",
            ],
        )

    assert result.exit_code == 0, result.output
    main_nf = outdir / "samtools" / "main.nf"
    assert main_nf.exists()
    content = main_nf.read_text()
    assert "TODO" in content, (
        f"Expected 'TODO' stub placeholder in main.nf\nActual:\n{content}"
    )
    yaml.safe_load((outdir / "samtools" / "meta.yml").read_text())


# ── Scenario D: --container stub flag overrides default ───────────────────────


def test_e2e_container_flag_stub_override(tmp_path: Path) -> None:
    """Scenario D: BioContainers available but --container stub forces STUB."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "samtools.py").write_text(_SAMTOOLS_ARGPARSE)

    outdir = tmp_path / "out"

    runner = CliRunner()
    with (
        patch(
            "httpx.get",
            side_effect=_make_httpx_mock(bioconda_200=["samtools"]),
        ),
        patch(
            "code_to_module.api.infer_module_spec_sync",
            side_effect=_infer_side_effect,
        ),
    ):
        result = runner.invoke(
            main,
            [
                "convert",
                str(repo / "samtools.py"),
                "--outdir",
                str(outdir),
                "--no-interaction",
                "--no-update-check",
                "--container",
                "stub",
            ],
        )

    assert result.exit_code == 0, result.output
    content = (outdir / "samtools" / "main.nf").read_text()
    # STUB always has TODO in the URL
    assert "TODO" in content, (
        f"Expected STUB with TODO when --container stub used\nActual:\n{content}"
    )


# ── Scenario F: multi-click, all three functionalities generated ───────────────


def test_e2e_multi_click_all(tmp_path: Path) -> None:
    """Scenario F: click repo with 3 commands → 3 module directories created."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "cli.py").write_text(_CLICK_THREE_COMMANDS)

    outdir = tmp_path / "out"

    runner = CliRunner()
    with (
        patch("httpx.get", side_effect=_make_httpx_mock()),  # all 404 → STUB
        patch(
            "code_to_module.api.infer_module_spec_sync",
            side_effect=_infer_side_effect,
        ),
    ):
        result = runner.invoke(
            main,
            [
                "convert",
                str(repo),
                "--outdir",
                str(outdir),
                "--no-interaction",
                "--no-update-check",
                "--all-functionalities",
            ],
        )

    assert result.exit_code == 0, result.output
    # All three click commands should produce module directories
    for name in ("sort", "index", "align"):
        mod_dir = outdir / name
        assert mod_dir.is_dir(), f"Expected module dir '{name}/' not found"
        assert (mod_dir / "main.nf").exists(), f"main.nf missing in {name}/"
        assert (mod_dir / "meta.yml").exists(), f"meta.yml missing in {name}/"
        yaml.safe_load((mod_dir / "meta.yml").read_text())


# ── Scenario G: --functionalities subset ──────────────────────────────────────


def test_e2e_multi_click_subset(tmp_path: Path) -> None:
    """Scenario G: --functionalities sort,index → only 2 modules, no align/."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "cli.py").write_text(_CLICK_THREE_COMMANDS)

    outdir = tmp_path / "out"

    runner = CliRunner()
    with (
        patch("httpx.get", side_effect=_make_httpx_mock()),
        patch(
            "code_to_module.api.infer_module_spec_sync",
            side_effect=_infer_side_effect,
        ),
    ):
        result = runner.invoke(
            main,
            [
                "convert",
                str(repo),
                "--outdir",
                str(outdir),
                "--no-interaction",
                "--no-update-check",
                "--functionalities",
                "sort,index",
            ],
        )

    assert result.exit_code == 0, result.output
    assert (outdir / "sort").is_dir(), "sort/ module dir not generated"
    assert (outdir / "index").is_dir(), "index/ module dir not generated"
    assert not (outdir / "align").exists(), "align/ should NOT be generated"
    yaml.safe_load((outdir / "sort" / "meta.yml").read_text())
    yaml.safe_load((outdir / "index" / "meta.yml").read_text())


# ── Scenario H: mixed tiers — one skipped ─────────────────────────────────────


def test_e2e_mixed_tier_skip(tmp_path: Path) -> None:
    """Scenario H: sort → Tier 1 (generated), index → Tier 5 (skipped) → exit 0."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # _CLICK_TWO_MIXED_TIER: sort has 8+ lines, index has 3 lines → Tier 5
    (repo / "cli.py").write_text(_CLICK_TWO_MIXED_TIER)

    outdir = tmp_path / "out"

    runner = CliRunner()
    with (
        # bioconda_200=["samtools"] lets assess reach Tier 1 for "sort"
        # bioconda_200=["sort"] lets container find BioContainers for func.name "sort"
        patch(
            "httpx.get",
            side_effect=_make_httpx_mock(bioconda_200=["samtools", "sort"]),
        ),
        patch(
            "code_to_module.api.infer_module_spec_sync",
            side_effect=_infer_side_effect,
        ),
    ):
        result = runner.invoke(
            main,
            [
                "convert",
                str(repo),
                "--outdir",
                str(outdir),
                "--no-interaction",
                "--no-update-check",
                "--all-functionalities",
            ],
        )

    # "sort" was generated, "index" was skipped (Tier 5) → partial success → exit 0
    assert result.exit_code == 0, result.output
    assert (outdir / "sort").is_dir(), "sort/ module was not generated"
    assert not (outdir / "index").exists(), "index/ should have been skipped (Tier 5)"
    # sort's main.nf should reference quay.io (BioContainers selected, Tier 1)
    s = get_standards()
    content = (outdir / "sort" / "main.nf").read_text()
    assert f"{s.docker_registry}sort" in content, (
        f"Expected quay.io BioContainers URL for sort\nActual:\n{content}"
    )
    yaml.safe_load((outdir / "sort" / "meta.yml").read_text())


# ── Scenario: meta.yml and main.nf always present ─────────────────────────────


def test_e2e_generated_files_parseable(tmp_path: Path) -> None:
    """All generated files must be well-formed text / valid YAML."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "samtools.py").write_text(_SAMTOOLS_ARGPARSE)

    outdir = tmp_path / "out"

    runner = CliRunner()
    with (
        patch("httpx.get", side_effect=_make_httpx_mock(bioconda_200=["samtools"])),
        patch(
            "code_to_module.api.infer_module_spec_sync",
            side_effect=_infer_side_effect,
        ),
    ):
        result = runner.invoke(
            main,
            [
                "convert",
                str(repo / "samtools.py"),
                "--outdir",
                str(outdir),
                "--no-interaction",
                "--no-update-check",
            ],
        )

    assert result.exit_code == 0, result.output
    mod_dir = outdir / "samtools"
    # main.nf: must be non-empty text containing the process block
    main_nf_text = (mod_dir / "main.nf").read_text()
    assert "process" in main_nf_text
    assert "input:" in main_nf_text
    assert "output:" in main_nf_text
    # meta.yml: must parse as a YAML dict with 'name' key
    meta = yaml.safe_load((mod_dir / "meta.yml").read_text())
    assert isinstance(meta, dict), "meta.yml must be a YAML mapping"
    assert "name" in meta, "meta.yml must have a 'name' key"
    # environment.yml: must parse
    yaml.safe_load((mod_dir / "environment.yml").read_text())
    # tests/main.nf.test: must be non-empty
    nftest = (mod_dir / "tests" / "main.nf.test").read_text()
    assert "process" in nftest.lower() or "nextflow_script" in nftest.lower()
