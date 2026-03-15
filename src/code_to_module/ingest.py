"""Ingest a source (file, directory, or Git URL) into a CodeSource."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import httpx
from git import Repo
from rich.console import Console
from ruamel.yaml import YAML

from code_to_module.models import (
    CodeSource,
    ContainerHint,
    DocSource,
    ExistingModule,
    RepoFile,
)

_console = Console()
_yaml = YAML()
_yaml.preserve_quotes = True

_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".R": "R",
    ".r": "R",
    ".sh": "bash",
    ".bash": "bash",
    ".pl": "perl",
    ".rb": "ruby",
    ".js": "javascript",
    ".ts": "javascript",
}

_SKIP_NAME_FRAGMENTS = frozenset(
    ["test", "spec", "util", "helper", "common", "config", "setup", "__init__", "conftest"]
)
_SKIP_DIRS = frozenset(["test", "tests", "spec", "docs", "examples", ".github"])

_MAX_CONTENT_CHARS = 4000
_MAX_MANIFEST_FILES = 50
_MAX_NON_ENTRYPOINT = 20


# ── Public API ────────────────────────────────────────────────────────────────


def ingest(
    source: str,
    docs: list[str] | None = None,
    existing_modules: list[str] | None = None,
) -> CodeSource:
    """Ingest *source* (file path, directory, or Git URL) into a CodeSource."""
    doc_sources = _fetch_doc_sources(docs or [])
    existing = _load_existing_modules(existing_modules or [])

    src_path = Path(source)

    if src_path.is_file():
        return _ingest_file(src_path, doc_sources, existing)
    if src_path.is_dir():
        return _ingest_directory(src_path, doc_sources, existing)
    if _is_git_url(source):
        return _ingest_git(source, doc_sources, existing)

    raise ValueError(
        f"Cannot read source {source!r}. "
        "Provide an existing file path, directory, or a Git URL "
        "(starting with https://, git@, or ending with .git)."
    )


# ── Doc fetching ──────────────────────────────────────────────────────────────


def _fetch_doc_sources(docs: list[str]) -> list[DocSource]:
    """Fetch each entry in *docs* and return one DocSource per entry."""
    results: list[DocSource] = []
    for entry in docs:
        if entry.startswith("http://") or entry.startswith("https://"):
            ds = _fetch_url(entry)
        else:
            ds = _read_file_doc(entry)
        results.append(ds)
    return results


def _fetch_url(url: str) -> DocSource:
    try:
        resp = httpx.get(url, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
        content = resp.text[:_MAX_CONTENT_CHARS]
        _console.print(f"📄 docs: {url} ✓")
        return DocSource(url=url, content=content, source_type="url")
    except Exception as exc:
        err = str(exc)
        _console.print(f"📄 docs: {url} ✗ {err}")
        return DocSource(url=url, content="", source_type="url", fetch_error=err)


def _read_file_doc(path_str: str) -> DocSource:
    p = Path(path_str)
    try:
        content = p.read_text(encoding="utf-8", errors="replace")[:_MAX_CONTENT_CHARS]
        _console.print(f"📄 docs: {path_str} ✓")
        return DocSource(path=p, content=content, source_type="file")
    except FileNotFoundError:
        err = "file not found"
        _console.print(f"📄 docs: {path_str} ✗ {err}")
        return DocSource(path=p, content="", source_type="file", fetch_error=err)
    except Exception as exc:
        err = str(exc)
        _console.print(f"📄 docs: {path_str} ✗ {err}")
        return DocSource(path=p, content="", source_type="file", fetch_error=err)


# ── Existing module parsing ───────────────────────────────────────────────────


def _load_existing_modules(paths: list[str]) -> list[ExistingModule]:
    """Parse each existing nf-core module directory and return one ExistingModule per path."""
    results: list[ExistingModule] = []
    for path_str in paths:
        results.append(_parse_module(path_str))
    return results


def _parse_module(path_str: str) -> ExistingModule:
    module_path = Path(path_str)
    main_nf = module_path / "main.nf"
    try:
        text = main_nf.read_text(encoding="utf-8")
        process_name = _extract_process_name(text)
        parts = process_name.split("_")
        tool_name = parts[0].lower()
        subcommand = "_".join(parts[1:]).lower() if len(parts) > 1 else None

        docker_url = _extract_docker_url(text)
        singularity_url = _extract_singularity_url(text)
        label = _extract_label(text)
        inputs = _extract_channel_names(text, block="input")
        outputs = _extract_channel_names(text, block="output")

        _console.print(f"📦 existing: {path_str} ✓")
        return ExistingModule(
            path=module_path,
            tool_name=tool_name,
            subcommand=subcommand or None,
            process_name=process_name,
            container_docker=docker_url,
            container_singularity=singularity_url,
            label=label,
            inputs=inputs,
            outputs=outputs,
        )
    except Exception as exc:
        err = str(exc)
        _console.print(f"📦 existing: {path_str} ✗ {err}")
        return ExistingModule(
            path=module_path,
            tool_name="",
            process_name="",
            container_docker="",
            container_singularity="",
            label="",
            load_error=err,
        )


def _extract_process_name(text: str) -> str:
    m = re.search(r"^process\s+(\w+)", text, re.MULTILINE)
    if not m:
        raise ValueError("No process declaration found in main.nf")
    return m.group(1)


def _extract_docker_url(text: str) -> str:
    m = re.search(r"['\"]?(quay\.io/biocontainers/[^\s'\"\\]+)['\"]?", text)
    return m.group(1) if m else ""


def _extract_singularity_url(text: str) -> str:
    m = re.search(r"['\"]?(https://depot\.galaxyproject\.org/singularity/[^\s'\"\\]+)['\"]?", text)
    if m:
        return m.group(1)
    m = re.search(r"['\"]?(docker://[^\s'\"\\]+)['\"]?", text)
    return m.group(1) if m else ""


def _extract_label(text: str) -> str:
    m = re.search(r"label\s+['\"](\w+)['\"]", text)
    return m.group(1) if m else ""


def _extract_channel_names(text: str, block: str) -> list[str]:
    """Extract `emit: name` values from an input: or output: block."""
    block_re = re.compile(rf"^\s*{block}:\s*$", re.MULTILINE)
    m = block_re.search(text)
    if not m:
        return []

    start = m.end()
    next_block = re.search(
        r"^\s*(output|when|script|stub|shell|exec):\s*$",
        text[start:],
        re.MULTILINE,
    )
    end = start + next_block.start() if next_block else len(text)
    block_text = text[start:end]

    return [em.group(1) for em in re.finditer(r"emit:\s*(\w+)", block_text)]


# ── Manifest & container hints ────────────────────────────────────────────────


def _build_manifest(repo_root: Path) -> list[RepoFile]:
    """Walk repo_root and return a sorted, capped file list.

    Scanning rules:
    - If a ``src/`` directory exists at the repo root (PEP 517 src-layout),
      scan it recursively with no depth limit — it is the entire source tree.
    - All other paths are limited to depth ≤ 3 to catch nested packages while
      avoiding deep virtual-env or build artefact trees.
    - _SKIP_DIRS filtering is applied per-file via _is_likely_entrypoint.
    """
    files: list[RepoFile] = []
    has_src_layout = (repo_root / "src").is_dir()

    for item in repo_root.rglob("*"):
        if not item.is_file():
            continue
        rel = item.relative_to(repo_root)
        depth = len(rel.parts) - 1

        if has_src_layout and rel.parts[0] == "src":
            pass  # scan the entire src/ tree regardless of depth
        elif depth > 3:
            continue

        lang = _LANG_MAP.get(item.suffix, "other")
        size = item.stat().st_size
        is_ep = _is_likely_entrypoint(item, rel, lang)

        files.append(RepoFile(path=rel, language=lang, size_bytes=size, is_likely_entrypoint=is_ep))

    files.sort(key=lambda f: (not f.is_likely_entrypoint, -f.size_bytes))

    if len(files) > _MAX_MANIFEST_FILES:
        entrypoints = [f for f in files if f.is_likely_entrypoint]
        others = [f for f in files if not f.is_likely_entrypoint]
        files = entrypoints + others[:_MAX_NON_ENTRYPOINT]

    return files


def _is_likely_entrypoint(item: Path, rel: Path, lang: str) -> bool:
    if lang == "other":
        return False
    if item.name.startswith("."):
        return False
    # Skip if any parent directory is in the skip list
    if any(part.lower() in _SKIP_DIRS for part in rel.parts[:-1]):
        return False
    # Skip if the filename stem contains any skip fragment
    stem_lower = item.stem.lower()
    if any(frag in stem_lower for frag in _SKIP_NAME_FRAGMENTS):
        return False
    return True


def _scan_container_hints(repo_root: Path) -> ContainerHint:
    """Scan repo_root for container-related files and return a ContainerHint."""
    hint = ContainerHint()

    dockerfile = repo_root / "Dockerfile"
    if dockerfile.is_file():
        hint.has_dockerfile = True
        hint.dockerfile_path = dockerfile
        hint.base_image = _extract_base_image(dockerfile)

    env_yml = repo_root / "environment.yml"
    if env_yml.is_file():
        hint.has_environment_yml = True
        hint.environment_yml_path = env_yml
        hint.conda_packages, hint.pip_packages = _parse_env_yml(env_yml)

    reqs = repo_root / "requirements.txt"
    if reqs.is_file():
        hint.has_requirements_txt = True
        if not hint.pip_packages:
            hint.pip_packages = _parse_requirements_txt(reqs)

    sing = repo_root / "Singularity.def"
    if sing.is_file():
        hint.has_singularity_def = True

    return hint


def _extract_base_image(dockerfile: Path) -> str | None:
    try:
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("FROM "):
                parts = stripped.split()
                if len(parts) >= 2:
                    return parts[1]
    except Exception:
        pass
    return None


def _parse_env_yml(env_yml: Path) -> tuple[list[str], list[str]]:
    conda_pkgs: list[str] = []
    pip_pkgs: list[str] = []
    try:
        data = _yaml.load(env_yml.read_text(encoding="utf-8"))
        deps = data.get("dependencies", []) if isinstance(data, dict) else []
        for dep in deps:
            if isinstance(dep, str):
                conda_pkgs.append(dep)
            elif isinstance(dep, dict):
                for val in dep.values():
                    if isinstance(val, list):
                        pip_pkgs.extend(str(v) for v in val)
    except Exception:
        pass
    return conda_pkgs, pip_pkgs


def _parse_requirements_txt(reqs: Path) -> list[str]:
    pkgs: list[str] = []
    try:
        for line in reqs.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                pkgs.append(line)
    except Exception:
        pass
    return pkgs


# ── Source-type-specific ingestion ────────────────────────────────────────────


def _ingest_file(
    src_path: Path,
    doc_sources: list[DocSource],
    existing: list[ExistingModule],
) -> CodeSource:
    lang = _LANG_MAP.get(src_path.suffix, "other")
    raw_code = src_path.read_text(encoding="utf-8", errors="replace")
    repo_root = src_path.parent
    manifest = _build_manifest(repo_root)
    hint = _scan_container_hints(repo_root)
    return CodeSource(
        source_type="file",
        path=src_path,
        language=lang,
        raw_code=raw_code,
        filename=src_path.name,
        repo_root=repo_root,
        container_hint=hint,
        repo_manifest=manifest,
        doc_sources=doc_sources,
        existing_modules=existing,
    )


def _ingest_directory(
    src_path: Path,
    doc_sources: list[DocSource],
    existing: list[ExistingModule],
) -> CodeSource:
    manifest = _build_manifest(src_path)
    hint = _scan_container_hints(src_path)
    primary = _pick_primary_script(manifest)
    if primary is None:
        raise ValueError(
            "No Python source files found in repo — cannot infer entry points. "
            "If this is not a Python tool, support for R and other languages is coming."
        )
    raw_code = (src_path / primary.path).read_text(encoding="utf-8", errors="replace")
    lang = primary.language
    filename = primary.path.name
    return CodeSource(
        source_type="directory",
        path=src_path,
        language=lang,
        raw_code=raw_code,
        filename=filename,
        repo_root=src_path,
        container_hint=hint,
        repo_manifest=manifest,
        doc_sources=doc_sources,
        existing_modules=existing,
    )


def _ingest_git(
    url: str,
    doc_sources: list[DocSource],
    existing: list[ExistingModule],
) -> CodeSource:
    tmp_dir = tempfile.mkdtemp(prefix="code-to-module-")
    repo_root = Path(tmp_dir)
    with _console.status(f"[bold green]Cloning {url}…"):
        Repo.clone_from(url, tmp_dir, depth=1)

    manifest = _build_manifest(repo_root)
    hint = _scan_container_hints(repo_root)
    primary = _pick_primary_script(manifest)
    if primary is None:
        raise ValueError(
            "No Python source files found in repo — cannot infer entry points. "
            "If this is not a Python tool, support for R and other languages is coming."
        )
    raw_code = (repo_root / primary.path).read_text(encoding="utf-8", errors="replace")
    lang = primary.language
    filename = primary.path.name
    return CodeSource(
        source_type="git",
        url=url,
        language=lang,
        raw_code=raw_code,
        filename=filename,
        repo_root=repo_root,
        container_hint=hint,
        repo_manifest=manifest,
        doc_sources=doc_sources,
        existing_modules=existing,
    )


_CLI_NAMES = frozenset(["cli", "command_line", "__main__", "main", "run", "app"])


def _pick_primary_script(manifest: list[RepoFile]) -> RepoFile | None:
    """Return the highest-confidence Python entrypoint, or the largest Python file.

    Priority order:
      1. Likely entrypoints whose stem is a well-known CLI entry-point name
         (cli, command_line, __main__, main, run, app).
      2. Any likely entrypoint (sorted by descending size — manifest ordering).
      3. Any Python file with a CLI entry-point name (handles package repos where
         cli.py / command_line.py is inside a package dir and therefore not marked
         as is_likely_entrypoint).
      4. Largest Python file.

    Returns None if the manifest contains no Python source files at all.
    Non-Python files (RST, YAML, TOML, etc.) are never selected.
    """
    if not manifest:
        return None
    # 1. Entrypoint with known CLI name
    for f in manifest:
        if f.is_likely_entrypoint and f.path.stem.lower() in _CLI_NAMES:
            return f
    # 2. Any likely entrypoint
    for f in manifest:
        if f.is_likely_entrypoint:
            return f
    # 3. Python file with known CLI name (package submodule case)
    for f in manifest:
        if f.language == "python" and f.path.stem.lower() in _CLI_NAMES:
            return f
    # 4. Fallback: largest Python file
    python_files = [f for f in manifest if f.language == "python"]
    if not python_files:
        return None
    return python_files[0]  # manifest is already sorted by -size_bytes


# ── Helpers ───────────────────────────────────────────────────────────────────


def _is_git_url(source: str) -> bool:
    return (
        source.startswith("https://")
        or source.startswith("git@")
        or source.endswith(".git")
    )
