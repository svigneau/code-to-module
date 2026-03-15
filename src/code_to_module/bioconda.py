"""Check Bioconda for existing packages and generate meta.yaml recipe scaffolds."""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel

from code_to_module.models import CodeSource

_ANACONDA_API = "https://api.anaconda.org/package/bioconda/{tool_name}"

_LICENSE_SPDX: list[tuple[str, str]] = [
    ("mit", "MIT"),
    ("apache license 2", "Apache-2.0"),
    ("apache 2", "Apache-2.0"),
    ("apache-2", "Apache-2.0"),
    ("gnu general public license v3", "GPL-3.0-only"),
    ("gnu general public license v2", "GPL-2.0-only"),
    ("gpl-3", "GPL-3.0-only"),
    ("gpl v3", "GPL-3.0-only"),
    ("gpl 3", "GPL-3.0-only"),
    ("gpl-2", "GPL-2.0-only"),
    ("gpl v2", "GPL-2.0-only"),
    ("gpl 2", "GPL-2.0-only"),
    ("lgpl", "LGPL-2.1-or-later"),
    ("bsd 3", "BSD-3-Clause"),
    ("bsd-3", "BSD-3-Clause"),
    ("bsd 2", "BSD-2-Clause"),
    ("bsd-2", "BSD-2-Clause"),
    ("bsd", "BSD-3-Clause"),
    ("cc by", "CC-BY-4.0"),
]


# ── Models ────────────────────────────────────────────────────────────────────


class BiocondaStatus(BaseModel):
    tool_name: str
    exists: bool
    latest_version: str | None = None
    biocontainers_url: str | None = None
    checked_at: datetime


class BiocondaRecipe(BaseModel):
    tool_name: str
    meta_yaml_content: str
    source_url: str | None = None
    version: str
    license: str
    dependencies: list[str] = []
    warnings: list[str] = []


# ── Public API ────────────────────────────────────────────────────────────────


def check_bioconda(tool_name: str) -> BiocondaStatus:
    """Query anaconda.org to check whether *tool_name* has a Bioconda package."""
    url = _ANACONDA_API.format(tool_name=tool_name)
    now = datetime.now(tz=timezone.utc)
    try:
        resp = httpx.get(url, timeout=10.0, follow_redirects=True)
        if resp.status_code == 200:
            data = resp.json()
            latest = data.get("latest_version")
            biocontainers = (
                f"quay.io/biocontainers/{tool_name}:{latest}--0"
                if latest
                else f"quay.io/biocontainers/{tool_name}"
            )
            return BiocondaStatus(
                tool_name=tool_name,
                exists=True,
                latest_version=latest,
                biocontainers_url=biocontainers,
                checked_at=now,
            )
        return BiocondaStatus(tool_name=tool_name, exists=False, checked_at=now)
    except Exception:
        return BiocondaStatus(tool_name=tool_name, exists=False, checked_at=now)


def generate_recipe(source: CodeSource, tool_name: str) -> BiocondaRecipe | None:
    """Generate a Bioconda meta.yaml scaffold from *source* metadata."""
    warnings: list[str] = []
    repo_root = source.repo_root

    version, ver_warnings = _detect_version(source)
    warnings.extend(ver_warnings)

    license_id, lic_warnings = _detect_license(repo_root)
    warnings.extend(lic_warnings)

    dependencies = _detect_dependencies(source)
    source_url = _detect_source_url(source, tool_name, version)

    meta_yaml = _render_meta_yaml(tool_name, version, source_url, license_id, dependencies)

    return BiocondaRecipe(
        tool_name=tool_name,
        meta_yaml_content=meta_yaml,
        source_url=source_url,
        version=version,
        license=license_id,
        dependencies=dependencies,
        warnings=warnings,
    )


# ── Version detection ─────────────────────────────────────────────────────────


def _detect_version(source: CodeSource) -> tuple[str, list[str]]:
    repo_root = source.repo_root

    if repo_root:
        v = _version_from_git_tags(repo_root)
        if v:
            return v, []

        v = _version_from_config_files(repo_root)
        if v:
            return v, []

    v = _version_from_code(source.raw_code)
    if v:
        return v, []

    return "0.1.0", ["Could not determine version — using placeholder '0.1.0'"]


def _version_from_git_tags(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "tag", "--sort=-v:refname"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        for tag in result.stdout.splitlines():
            tag = tag.strip()
            m = re.match(r"^v?(\d+\.\d+[\.\d]*)$", tag)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def _version_from_config_files(repo_root: Path) -> str | None:
    for filename in ("setup.py", "pyproject.toml", "setup.cfg"):
        f = repo_root / filename
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'version\s*[=:]\s*["\']?([0-9][^\s"\',}\]]+)["\']?', text)
        if m:
            return m.group(1).strip()
    return None


def _version_from_code(raw_code: str) -> str | None:
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', raw_code)
    return m.group(1) if m else None


# ── License detection ─────────────────────────────────────────────────────────


def _detect_license(repo_root: Path | None) -> tuple[str, list[str]]:
    if repo_root is not None:
        for name in ("LICENSE", "LICENSE.txt", "LICENSE.md"):
            f = repo_root / name
            if f.is_file():
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
                first_line = lines[0].strip() if lines else ""
                return _map_license(first_line), []
    return "TODO: add license", ["Could not find LICENSE file — add license manually"]


def _map_license(text: str) -> str:
    t = text.lower()
    for fragment, spdx in _LICENSE_SPDX:
        if fragment in t:
            return spdx
    return text  # return as-is if no match found


# ── Dependency detection ──────────────────────────────────────────────────────


def _detect_dependencies(source: CodeSource) -> list[str]:
    deps: list[str] = []
    hint = source.container_hint

    if source.language == "python":
        deps.append("python")

    if hint and hint.conda_packages:
        for pkg in hint.conda_packages:
            name = re.split(r"[>=<!,\s]", pkg)[0].strip()
            if name and name != "python":
                deps.append(name)
    elif hint and hint.pip_packages:
        for pkg in hint.pip_packages:
            name = re.split(r"[>=<!,\s]", pkg)[0].strip()
            if name:
                deps.append(name)
    elif source.repo_root:
        deps.extend(_deps_from_config_files(source.repo_root))

    # Deduplicate, preserving order
    seen: set[str] = set()
    result: list[str] = []
    for d in deps:
        if d not in seen:
            seen.add(d)
            result.append(d)
    return result


def _deps_from_config_files(repo_root: Path) -> list[str]:
    """Parse install_requires from setup.py or dependencies from pyproject.toml."""
    deps: list[str] = []

    setup_py = repo_root / "setup.py"
    if setup_py.is_file():
        text = setup_py.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"install_requires\s*=\s*\[([^\]]+)\]", text, re.DOTALL)
        if m:
            for item in re.findall(r'["\']([^"\']+)["\']', m.group(1)):
                pkg = re.split(r"[>=<!,\s]", item)[0].strip()
                if pkg:
                    deps.append(pkg)

    pyproject = repo_root / "pyproject.toml"
    if pyproject.is_file() and not deps:
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"dependencies\s*=\s*\[([^\]]+)\]", text, re.DOTALL)
        if m:
            for item in re.findall(r'["\']([^"\']+)["\']', m.group(1)):
                pkg = re.split(r"[>=<!,\s]", item)[0].strip()
                if pkg:
                    deps.append(pkg)

    return deps


# ── Source URL detection ──────────────────────────────────────────────────────


def _detect_source_url(source: CodeSource, tool_name: str, version: str) -> str:
    if source.repo_root:
        url = _url_from_config_files(source.repo_root)
        if url:
            return url
    if source.url:
        return source.url
    return f"https://TODO.replace.me/with/your/release/tarball/{version}.tar.gz"


def _url_from_config_files(repo_root: Path) -> str | None:
    for filename in ("setup.py", "pyproject.toml"):
        f = repo_root / filename
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'url\s*=\s*["\']([^"\']+)["\']', text)
        if m:
            url = m.group(1)
            if url.startswith("http"):
                return url
    return None


# ── Rendering ─────────────────────────────────────────────────────────────────


def _render_meta_yaml(
    tool_name: str,
    version: str,
    source_url: str,
    license_id: str,
    dependencies: list[str],
) -> str:
    run_deps = ["python"] + [d for d in dependencies if d != "python"]
    run_deps_yaml = "\n    ".join(f"- {d}" for d in run_deps)

    return (
        f"package:\n"
        f"  name: {tool_name}\n"
        f'  version: "{version}"\n'
        f"\n"
        f"source:\n"
        f"  url: {source_url}\n"
        f"  sha256: TODO\n"
        f"\n"
        f"build:\n"
        f"  number: 0\n"
        f"  script: pip install . --no-deps --no-build-isolation\n"
        f"\n"
        f"requirements:\n"
        f"  host:\n"
        f"    - python\n"
        f"    - pip\n"
        f"  run:\n"
        f"    {run_deps_yaml}\n"
        f"\n"
        f"test:\n"
        f"  commands:\n"
        f"    - {tool_name} --help\n"
        f"\n"
        f"about:\n"
        f"  home: {source_url}\n"
        f"  license: '{license_id}'\n"
        f"  summary: 'TODO'\n"
        f"\n"
        f"extra:\n"
        f"  recipe-maintainers:\n"
        f"    - TODO\n"
    )
