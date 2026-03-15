"""Tests for container.discover() — Phase 1 container discovery.

container.py has no implementation yet.  Tests are skipped until discover() is
defined and will FAIL (not error/pass) against a stub that returns wrong values.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Conditional import ────────────────────────────────────────────────────────
# importorskip skips the whole module when container.py does not exist.
# The getattr guard skips when the module exists but discover() is not yet wired.

_container_mod = pytest.importorskip("code_to_module.container")
_discover = getattr(_container_mod, "discover", None)
if _discover is None:
    pytest.skip("container.discover not yet implemented", allow_module_level=True)

discover = _discover  # type: ignore[assignment]

from code_to_module.models import (  # noqa: E402
    CodeSource,
    ContainerHint,
    ContainerSource,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _bioconda_resp(status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {"latest_version": "1.0.0", "versions": ["1.0.0"]}
    return resp


def _source(
    tool_name: str = "mytool",
    *,
    has_dockerfile: bool = False,
    dockerfile_path: Path | None = None,
    has_environment_yml: bool = False,
    environment_yml_path: Path | None = None,
    has_requirements_txt: bool = False,
    requirements_txt_path: Path | None = None,
    has_singularity_def: bool = False,
    singularity_def_path: Path | None = None,
    source_url: str | None = None,
    source_path: Path | None = None,
) -> CodeSource:
    hint = ContainerHint(
        has_dockerfile=has_dockerfile,
        dockerfile_path=dockerfile_path,
        has_environment_yml=has_environment_yml,
        environment_yml_path=environment_yml_path,
        has_requirements_txt=has_requirements_txt,
        requirements_txt_path=requirements_txt_path,
        has_singularity_def=has_singularity_def,
        singularity_def_path=singularity_def_path,
    )
    return CodeSource(
        source_type="git" if source_url else "file",
        url=source_url,
        path=source_path or Path("/fake/script.py"),
        language="python",
        raw_code="print('hello')",
        filename=f"{tool_name}.py",
        container_hint=hint,
    )


# ── Core behaviour ─────────────────────────────────────────────────────────────


def test_all_checks_always_run() -> None:
    """All checks run even when a Dockerfile is present — no early return allowed.

    This explicitly catches the "early return" bug where finding a Dockerfile
    skips the BioContainers check.
    """
    src = _source("samtools", has_dockerfile=True)

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(200)
        result = discover(src, "samtools", tier=1)

    sources = [opt.source for opt in result.options]
    assert ContainerSource.DOCKERFILE in sources, "DOCKERFILE missing — check ran but produced no option"
    assert ContainerSource.BIOCONTAINERS in sources, "BIOCONTAINERS missing — early return bug detected"


def test_stub_always_last_option() -> None:
    """STUB must be present in options regardless of what else was found."""
    src = _source("samtools", has_dockerfile=True)

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(200)
        result = discover(src, "samtools", tier=1)

    sources = [opt.source for opt in result.options]
    assert ContainerSource.STUB in sources


def test_stub_is_only_option_when_nothing_found() -> None:
    """No repo files and Bioconda 404 → exactly one option and it is STUB."""
    src = _source("unknowntool_xyz")

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(404)
        result = discover(src, "unknowntool_xyz", tier=3)

    assert len(result.options) == 1
    assert result.options[0].source == ContainerSource.STUB


# ── Tier-aware default selection ───────────────────────────────────────────────


def test_tier1_with_biocontainers_default_is_biocontainers() -> None:
    """Tier 1: BioContainers outranks Dockerfile as the default."""
    src = _source("samtools", has_dockerfile=True)

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(200)
        result = discover(src, "samtools", tier=1)

    default = next(opt for opt in result.options if opt.is_default)
    assert default.source == ContainerSource.BIOCONTAINERS


def test_tier2_with_biocontainers_default_is_biocontainers() -> None:
    """Tier 2 behaves identically to Tier 1: BioContainers is the default."""
    src = _source("samtools", has_dockerfile=True)

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(200)
        result = discover(src, "samtools", tier=2)

    default = next(opt for opt in result.options if opt.is_default)
    assert default.source == ContainerSource.BIOCONTAINERS


def test_tier3_with_dockerfile_default_is_dockerfile() -> None:
    """Tier 3+: Dockerfile outranks BioContainers as the default."""
    src = _source("custom_tool", has_dockerfile=True)

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(200)
        result = discover(src, "custom_tool", tier=3)

    default = next(opt for opt in result.options if opt.is_default)
    assert default.source == ContainerSource.DOCKERFILE


def test_tier3_without_dockerfile_biocontainers_is_default() -> None:
    """Tier 3, no Dockerfile: BioContainers is the default when available."""
    src = _source("samtools")  # no dockerfile

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(200)
        result = discover(src, "samtools", tier=3)

    default = next(opt for opt in result.options if opt.is_default)
    assert default.source == ContainerSource.BIOCONTAINERS


def test_tier3_envyml_default_when_no_dockerfile_no_bio(tmp_path: Path) -> None:
    """Tier 3, environment.yml present, Bioconda 404, no Dockerfile → GENERATED_FROM_ENVYML."""
    envyml = tmp_path / "environment.yml"
    envyml.write_text("name: mytool\ndependencies:\n  - mytool=1.0\n")

    src = _source(
        "mytool",
        has_environment_yml=True,
        environment_yml_path=envyml,
    )

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(404)
        result = discover(src, "mytool", tier=3)

    default = next(opt for opt in result.options if opt.is_default)
    assert default.source == ContainerSource.GENERATED_FROM_ENVYML


@pytest.mark.parametrize(
    "tier,has_dockerfile,bio_status",
    [
        (1, True, 200),   # Tier 1: BioContainers + Dockerfile
        (3, True, 200),   # Tier 3: Dockerfile + BioContainers
        (5, False, 404),  # Tier 5: stub only
    ],
)
def test_exactly_one_default(tier: int, has_dockerfile: bool, bio_status: int) -> None:
    """Exactly one option has is_default=True for any tier/option combination."""
    tool = "samtools" if bio_status == 200 else "unknowntool_xyz"
    src = _source(tool, has_dockerfile=has_dockerfile)

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(bio_status)
        result = discover(src, tool, tier=tier)

    defaults = [opt for opt in result.options if opt.is_default]
    assert len(defaults) == 1, (
        f"Expected exactly 1 default option at tier={tier}, got {len(defaults)}: "
        f"{[opt.source.value for opt in defaults]}"
    )


# ── Container URL format ───────────────────────────────────────────────────────


def test_biocontainers_docker_url_format() -> None:
    """BioContainers option docker_url starts with 'quay.io/biocontainers/'."""
    src = _source("samtools")

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(200)
        result = discover(src, "samtools", tier=1)

    bio = next(opt for opt in result.options if opt.source == ContainerSource.BIOCONTAINERS)
    assert bio.docker_url.startswith("quay.io/biocontainers/")


def test_biocontainers_singularity_url_format() -> None:
    """BioContainers singularity_url starts with depot.galaxyproject.org prefix."""
    src = _source("samtools")

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(200)
        result = discover(src, "samtools", tier=1)

    bio = next(opt for opt in result.options if opt.source == ContainerSource.BIOCONTAINERS)
    assert bio.singularity_url.startswith("https://depot.galaxyproject.org/singularity/")


def test_dockerfile_github_url_uses_ghcr() -> None:
    """Dockerfile from a GitHub repo URL → docker_url starts with 'ghcr.io/'."""
    src = _source(
        "mytool",
        has_dockerfile=True,
        source_url="https://github.com/myorg/mytool",
    )

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(404)
        result = discover(src, "mytool", tier=3)

    df = next(opt for opt in result.options if opt.source == ContainerSource.DOCKERFILE)
    assert df.docker_url.startswith("ghcr.io/")


def test_dockerfile_local_url_uses_tool_name(tmp_path: Path) -> None:
    """Dockerfile from a local path → docker_url starts with tool_name."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM ubuntu:22.04\n")

    src = _source(
        "mytool",
        has_dockerfile=True,
        dockerfile_path=dockerfile,
        source_path=tmp_path / "script.py",
    )

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(404)
        result = discover(src, "mytool", tier=3)

    df = next(opt for opt in result.options if opt.source == ContainerSource.DOCKERFILE)
    assert df.docker_url.startswith("mytool")


def test_stub_url_contains_todo() -> None:
    """STUB option docker_url contains 'TODO'."""
    src = _source("unknowntool_xyz")

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(404)
        result = discover(src, "unknowntool_xyz", tier=5)

    stub = next(opt for opt in result.options if opt.source == ContainerSource.STUB)
    assert "TODO" in stub.docker_url


# ── Network and caching ────────────────────────────────────────────────────────


def test_biocontainers_cache_second_call_no_http() -> None:
    """Second discover() call for same tool_name hits the cache — HTTP called once."""
    src = _source("samtools_cache_test")

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(200)
        discover(src, "samtools_cache_test", tier=1)
        discover(src, "samtools_cache_test", tier=1)

    # Both calls share the same mocked httpx, so call_count covers all calls.
    # Caching should reduce Bioconda HTTP calls to exactly 1 across both invocations.
    assert mock_http.get.call_count == 1, (
        f"Expected 1 HTTP call (cache hit on second call), got {mock_http.get.call_count}"
    )


def test_biocontainers_404_no_option_added() -> None:
    """Bioconda 404 → BIOCONTAINERS not added to options."""
    src = _source("unknowntool_xyz")

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(404)
        result = discover(src, "unknowntool_xyz", tier=3)

    sources = [opt.source for opt in result.options]
    assert ContainerSource.BIOCONTAINERS not in sources


def test_biocontainers_network_timeout_no_crash() -> None:
    """Network timeout → no exception raised and BIOCONTAINERS absent from options."""
    import httpx as _httpx

    src = _source("samtools")

    with patch("code_to_module.container.httpx") as mock_http:
        # Make TimeoutException resolvable via the patched module
        mock_http.TimeoutException = _httpx.TimeoutException
        mock_http.get.side_effect = _httpx.TimeoutException("timed out")
        result = discover(src, "samtools", tier=1)

    assert result is not None
    sources = [opt.source for opt in result.options]
    assert ContainerSource.BIOCONTAINERS not in sources


def test_github_tags_api_failure_uses_latest() -> None:
    """GitHub tags API 404 → Dockerfile option still created using tag 'latest'."""
    src = _source(
        "mytool",
        has_dockerfile=True,
        source_url="https://github.com/myorg/mytool",
    )

    with patch("code_to_module.container.httpx") as mock_http:
        # All HTTP calls return 404 (tags API and Bioconda)
        mock_http.get.return_value = _bioconda_resp(404)
        result = discover(src, "mytool", tier=3)

    df = next(opt for opt in result.options if opt.source == ContainerSource.DOCKERFILE)
    assert "latest" in df.docker_url


# ── Content tests ──────────────────────────────────────────────────────────────


def test_envyml_option_has_dockerfile_content(tmp_path: Path) -> None:
    """GENERATED_FROM_ENVYML option has non-None, non-empty dockerfile_content."""
    envyml = tmp_path / "environment.yml"
    envyml.write_text("name: mytool\ndependencies:\n  - mytool=1.0\n")

    src = _source("mytool", has_environment_yml=True, environment_yml_path=envyml)

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(404)
        result = discover(src, "mytool", tier=3)

    opt = next(
        opt for opt in result.options
        if opt.source == ContainerSource.GENERATED_FROM_ENVYML
    )
    assert opt.dockerfile_content is not None
    assert opt.dockerfile_content.strip() != ""


def test_envyml_dockerfile_content_contains_mamba(tmp_path: Path) -> None:
    """GENERATED_FROM_ENVYML dockerfile_content includes a mamba or conda install step."""
    envyml = tmp_path / "environment.yml"
    envyml.write_text("name: mytool\ndependencies:\n  - mytool=1.0\n")

    src = _source("mytool", has_environment_yml=True, environment_yml_path=envyml)

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(404)
        result = discover(src, "mytool", tier=3)

    opt = next(
        opt for opt in result.options
        if opt.source == ContainerSource.GENERATED_FROM_ENVYML
    )
    content = (opt.dockerfile_content or "").lower()
    assert "mamba" in content or "conda" in content


def test_singularity_def_conversion_has_run_command(tmp_path: Path) -> None:
    """Singularity.def with %post section → CONVERTED_FROM_SINGULARITY dockerfile_content has RUN."""
    singdef = tmp_path / "Singularity.def"
    singdef.write_text(
        "Bootstrap: docker\nFrom: ubuntu:22.04\n\n"
        "%post\n    apt-get update\n    apt-get install -y samtools\n"
    )

    src = _source(
        "mytool",
        has_singularity_def=True,
        singularity_def_path=singdef,
    )

    with patch("code_to_module.container.httpx") as mock_http:
        mock_http.get.return_value = _bioconda_resp(404)
        result = discover(src, "mytool", tier=3)

    opt = next(
        opt for opt in result.options
        if opt.source == ContainerSource.CONVERTED_FROM_SINGULARITY
    )
    assert "RUN" in (opt.dockerfile_content or "")
