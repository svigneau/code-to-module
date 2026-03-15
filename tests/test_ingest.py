"""Tests for ingest.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from code_to_module.ingest import ingest

FIXTURES = Path(__file__).parent / "fixtures"


# ── Single file ───────────────────────────────────────────────────────────────


def test_single_python_file(tmp_path):
    script = tmp_path / "mytool.py"
    script.write_text("def main(): pass\n")

    result = ingest(str(script))

    assert result.source_type == "file"
    assert result.language == "python"
    assert result.filename == "mytool.py"
    assert result.raw_code == "def main(): pass\n"
    assert len(result.repo_manifest) >= 1
    ep = next(f for f in result.repo_manifest if f.path.name == "mytool.py")
    assert ep.is_likely_entrypoint is True


# ── Directory with multiple scripts ──────────────────────────────────────────


def test_directory_with_multiple_scripts():
    result = ingest(str(FIXTURES / "repo_multi_scripts"))

    assert result.source_type == "directory"
    entrypoints = [f for f in result.repo_manifest if f.is_likely_entrypoint]
    assert len(entrypoints) == 3
    names = {f.path.name for f in entrypoints}
    assert names == {"align.py", "sort.py", "index.py"}


# ── Click repo manifest ───────────────────────────────────────────────────────


def test_click_repo_manifest():
    result = ingest(str(FIXTURES / "repo_click_multi"))

    entrypoints = [f for f in result.repo_manifest if f.is_likely_entrypoint]
    names = {f.path.name for f in entrypoints}
    assert "mytool.py" in names


# ── Test-file exclusion ───────────────────────────────────────────────────────


def test_excludes_test_files():
    result = ingest(str(FIXTURES / "repo_click_multi"))

    for f in result.repo_manifest:
        if "test" in str(f.path).lower():
            assert f.is_likely_entrypoint is False, (
                f"{f.path} should not be an entrypoint"
            )


# ── Container hints ───────────────────────────────────────────────────────────


def test_container_hints_dockerfile():
    result = ingest(str(FIXTURES / "repo_with_dockerfile"))

    assert result.container_hint is not None
    assert result.container_hint.has_dockerfile is True
    assert result.container_hint.dockerfile_path is not None


def test_container_hints_envyml():
    result = ingest(str(FIXTURES / "repo_with_envyml"))

    hint = result.container_hint
    assert hint is not None
    assert hint.has_environment_yml is True
    assert "samtools=1.17" in hint.conda_packages
    assert any("requests" in p for p in hint.pip_packages)


# ── Git URL ───────────────────────────────────────────────────────────────────


def test_git_url_clones():
    mock_repo = MagicMock()
    fake_script = None

    def fake_clone(url, path, depth=None):
        # Write a minimal script into the temp dir so the rest of ingest works
        nonlocal fake_script
        fake_script = Path(path) / "tool.py"
        fake_script.write_text("def main(): pass\n")
        return mock_repo

    with patch("code_to_module.ingest.Repo.clone_from", side_effect=fake_clone):
        result = ingest("https://github.com/example/mytool.git")

    assert result.source_type == "git"
    assert result.url == "https://github.com/example/mytool.git"
    assert result.language == "python"


# ── Invalid path ─────────────────────────────────────────────────────────────


def test_invalid_path_raises():
    with pytest.raises(ValueError, match="Cannot read source"):
        ingest("/no/such/path/anywhere")


# ── --docs: URL fetched ───────────────────────────────────────────────────────


def test_docs_url_fetched(tmp_path):
    script = tmp_path / "tool.py"
    script.write_text("def main(): pass\n")

    mock_resp = MagicMock()
    mock_resp.text = "# Tool documentation\nUsage: tool [OPTIONS]"
    mock_resp.raise_for_status = MagicMock()

    with patch("code_to_module.ingest.httpx.get", return_value=mock_resp):
        result = ingest(str(script), docs=["https://example.com/readme"])

    assert len(result.doc_sources) == 1
    ds = result.doc_sources[0]
    assert ds.source_type == "url"
    assert ds.url == "https://example.com/readme"
    assert ds.content != ""
    assert ds.fetch_error is None


# ── --docs: local file ────────────────────────────────────────────────────────


def test_docs_file_read(tmp_path):
    script = tmp_path / "tool.py"
    script.write_text("def main(): pass\n")
    readme = tmp_path / "README.md"
    readme.write_text("# My Tool\nThis tool does stuff.")

    result = ingest(str(script), docs=[str(readme)])

    assert len(result.doc_sources) == 1
    ds = result.doc_sources[0]
    assert ds.source_type == "file"
    assert "My Tool" in ds.content
    assert ds.fetch_error is None


# ── --docs: fetch failure ─────────────────────────────────────────────────────


def test_docs_fetch_failure(tmp_path):
    script = tmp_path / "tool.py"
    script.write_text("def main(): pass\n")

    import httpx as _httpx

    with patch("code_to_module.ingest.httpx.get", side_effect=_httpx.TimeoutException("timed out")):
        # Must NOT raise
        result = ingest(str(script), docs=["https://example.com/readme"])

    assert len(result.doc_sources) == 1
    ds = result.doc_sources[0]
    assert ds.content == ""
    assert ds.fetch_error is not None


# ── --docs: empty by default ──────────────────────────────────────────────────


def test_docs_empty_by_default(tmp_path):
    script = tmp_path / "tool.py"
    script.write_text("def main(): pass\n")

    result = ingest(str(script))

    assert result.doc_sources == []


# ── --existing-modules: parsed ────────────────────────────────────────────────


def test_existing_module_parsed(tmp_path):
    script = tmp_path / "tool.py"
    script.write_text("def main(): pass\n")

    module_dir = FIXTURES / "modules" / "passing"
    result = ingest(str(script), existing_modules=[str(module_dir)])

    assert len(result.existing_modules) == 1
    em = result.existing_modules[0]
    assert em.process_name == "SAMTOOLS_SORT"
    assert em.tool_name == "samtools"
    assert em.subcommand == "sort"
    assert "quay.io/biocontainers" in em.container_docker
    assert em.label == "process_medium"
    assert em.load_error is None


# ── --existing-modules: parse error ──────────────────────────────────────────


def test_existing_module_parse_error(tmp_path):
    script = tmp_path / "tool.py"
    script.write_text("def main(): pass\n")

    empty_dir = tmp_path / "no_module_here"
    empty_dir.mkdir()

    # Must NOT raise
    result = ingest(str(script), existing_modules=[str(empty_dir)])

    assert len(result.existing_modules) == 1
    em = result.existing_modules[0]
    assert em.load_error is not None


# ── --existing-modules: empty by default ─────────────────────────────────────


def test_existing_modules_empty_by_default(tmp_path):
    script = tmp_path / "tool.py"
    script.write_text("def main(): pass\n")

    result = ingest(str(script))

    assert result.existing_modules == []
