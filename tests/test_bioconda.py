"""Tests for bioconda.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from code_to_module.bioconda import (
    BiocondaStatus,
    check_bioconda,
    generate_recipe,
)
from code_to_module.models import CodeSource, ContainerHint

# ── Helpers ───────────────────────────────────────────────────────────────────


def _minimal_source(tmp_path: Path, code: str = "def main(): pass\n") -> CodeSource:
    """Return a minimal CodeSource rooted at tmp_path."""
    script = tmp_path / "mytool.py"
    script.write_text(code)
    return CodeSource(
        source_type="file",
        language="python",
        raw_code=code,
        filename="mytool.py",
        repo_root=tmp_path,
        container_hint=ContainerHint(),
    )


def _mock_200(latest_version: str = "1.17") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"latest_version": latest_version, "name": "mytool"}
    return resp


def _mock_404() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 404
    return resp


# ── check_bioconda ────────────────────────────────────────────────────────────


def test_check_bioconda_exists():
    with patch("code_to_module.bioconda.httpx.get", return_value=_mock_200("1.17")):
        status = check_bioconda("samtools")

    assert isinstance(status, BiocondaStatus)
    assert status.exists is True
    assert status.latest_version == "1.17"
    assert status.biocontainers_url is not None
    assert "samtools" in status.biocontainers_url


def test_check_bioconda_missing():
    with patch("code_to_module.bioconda.httpx.get", return_value=_mock_404()):
        status = check_bioconda("unknown-tool-xyz")

    assert status.exists is False
    assert status.latest_version is None


def test_check_bioconda_network_error():
    import httpx as _httpx

    with patch(
        "code_to_module.bioconda.httpx.get",
        side_effect=_httpx.TimeoutException("timed out"),
    ):
        status = check_bioconda("samtools")  # must not raise

    assert status.exists is False


# ── generate_recipe: version detection ───────────────────────────────────────


def test_generate_recipe_from_setup_py(tmp_path):
    (tmp_path / "setup.py").write_text(
        "from setuptools import setup\n"
        "setup(\n"
        "    name='mytool',\n"
        "    version='1.0.0',\n"
        "    install_requires=['numpy>=1.20', 'scipy'],\n"
        ")\n"
    )
    source = _minimal_source(tmp_path)

    with patch("code_to_module.bioconda.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        recipe = generate_recipe(source, "mytool")

    assert recipe is not None
    assert recipe.version == "1.0.0"
    assert len(recipe.dependencies) >= 1


def test_generate_recipe_from_git_tag(tmp_path):
    source = _minimal_source(tmp_path)

    with patch("code_to_module.bioconda.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="v2.3.0\nv2.2.0\n", returncode=0)
        recipe = generate_recipe(source, "mytool")

    assert recipe is not None
    assert recipe.version == "2.3.0"


def test_generate_recipe_version_fallback(tmp_path):
    source = _minimal_source(tmp_path)

    with patch("code_to_module.bioconda.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        recipe = generate_recipe(source, "mytool")

    assert recipe is not None
    assert recipe.version == "0.1.0"
    assert any("placeholder" in w.lower() for w in recipe.warnings)


# ── generate_recipe: license detection ───────────────────────────────────────


def test_generate_recipe_license_mit(tmp_path):
    (tmp_path / "LICENSE").write_text("MIT License\n\nCopyright (c) 2024 Author\n")
    source = _minimal_source(tmp_path)

    with patch("code_to_module.bioconda.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        recipe = generate_recipe(source, "mytool")

    assert recipe is not None
    assert recipe.license == "MIT"


def test_generate_recipe_license_unknown(tmp_path):
    # No LICENSE file
    source = _minimal_source(tmp_path)

    with patch("code_to_module.bioconda.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        recipe = generate_recipe(source, "mytool")

    assert recipe is not None
    assert "TODO" in recipe.license


# ── generate_recipe: YAML validity ───────────────────────────────────────────


def test_meta_yaml_is_valid_yaml(tmp_path):
    source = _minimal_source(tmp_path)

    with patch("code_to_module.bioconda.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        recipe = generate_recipe(source, "mytool")

    assert recipe is not None
    # Must not raise
    parsed = yaml.safe_load(recipe.meta_yaml_content)
    assert parsed is not None


def test_meta_yaml_has_required_keys(tmp_path):
    source = _minimal_source(tmp_path)

    with patch("code_to_module.bioconda.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        recipe = generate_recipe(source, "mytool")

    assert recipe is not None
    parsed = yaml.safe_load(recipe.meta_yaml_content)
    for key in ("package", "source", "build", "requirements", "test", "about"):
        assert key in parsed, f"Missing top-level key: {key}"
