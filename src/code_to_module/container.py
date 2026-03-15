"""Two-phase container handling: discover all options, then select one.

Discovery priority order (run ALL checks in parallel, never short-circuit):
  1. Dockerfile in repo
  2. BioContainers (if tool is Tier 1-2, i.e. known Bioconda package)
  3. Generated from environment.yml      ← temporary; Bioconda preferred for known tools
  4. Generated from requirements.txt     ← temporary; Bioconda preferred for known tools
  5. Convert from Singularity.def
  6. Stub (fill in manually)

Tier-aware default selection:
  - Tier 1-2 (known Bioconda entry): BioContainers ranked ABOVE repo files.
    The community standard image is preferred for well-known tools.
  - Tier 3-5 (custom/unknown): Dockerfile or generated options ranked first.

BioContainers lookup failure note:
  If a Tier 1-2 tool's BioContainers lookup returns 404 (tool is known but
  lookup failed — possibly a version mismatch or network error), the caller
  should suggest:
    "Tool {tool_name} is known but BioContainers lookup failed.
     If this tool is not yet in Bioconda, run:
       code-to-module bioconda-recipe {source}"
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from jinja2 import Environment, FileSystemLoader
from rich.console import Console

from code_to_module.models import (
    CodeSource,
    ContainerDiscovery,
    ContainerOption,
    ContainerSource,
)
from code_to_module.standards import get_standards

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# Module-level cache: tool_name → ContainerOption | None
# None means "404 / lookup attempted but failed"
_biocontainers_cache: dict[str, ContainerOption | None] = {}


# ── Template rendering ─────────────────────────────────────────────────────────


def _render_dockerfile(source: str, **kwargs: str) -> str:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        keep_trailing_newline=True,
    )
    return env.get_template("Dockerfile.j2").render(source=source, **kwargs)


# ── GitHub helpers ─────────────────────────────────────────────────────────────


def _parse_github_url(url: str) -> tuple[str, str] | None:
    """Return (owner, repo) from a GitHub URL, or None if not GitHub."""
    parsed = urlparse(url)
    if parsed.netloc != "github.com":
        return None
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) >= 2:
        return parts[0], parts[1].removesuffix(".git")
    return None


def _get_github_tag(owner: str, repo: str) -> str:
    """Return the latest GitHub release tag name, or 'latest' on any failure."""
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{owner}/{repo}/tags",
            timeout=10,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            tags = resp.json()
            if tags:
                return str(tags[0]["name"])
    except httpx.TimeoutException:
        pass
    return "latest"


# ── Individual container checks ────────────────────────────────────────────────


def _check_dockerfile(source: CodeSource, tool_name: str) -> ContainerOption:
    hint = source.container_hint
    dockerfile_content: str | None = None
    if hint and hint.dockerfile_path:
        try:
            dockerfile_content = hint.dockerfile_path.read_text()
        except OSError:
            pass

    gh = _parse_github_url(source.url or "")
    if gh:
        owner, repo = gh
        tag = _get_github_tag(owner, repo)
        docker_url = f"ghcr.io/{owner}/{repo}:{tag}"
    else:
        docker_url = f"{tool_name}:latest"

    singularity_url = f"docker://{docker_url}"
    return ContainerOption(
        source=ContainerSource.DOCKERFILE,
        label="Dockerfile in repo",
        docker_url=docker_url,
        singularity_url=singularity_url,
        dockerfile_content=dockerfile_content,
        warnings=[f"Build and push before nf-core submission: docker build -t {docker_url} ."],
    )


def _check_envyml(source: CodeSource, tool_name: str) -> ContainerOption:
    hint = source.container_hint
    env_name = tool_name
    if hint and hint.environment_yml_path:
        try:
            text = hint.environment_yml_path.read_text()
            m = re.search(r"^name:\s*(\S+)", text, re.MULTILINE)
            if m:
                env_name = m.group(1)
        except OSError:
            pass

    gh = _parse_github_url(source.url or "")
    if gh:
        owner, _ = gh
        docker_url = f"ghcr.io/{owner}/{tool_name}:latest"
    else:
        docker_url = f"{tool_name}:latest"

    singularity_url = f"docker://{docker_url}"
    dockerfile_content = _render_dockerfile("envyml", env_name=env_name)
    return ContainerOption(
        source=ContainerSource.GENERATED_FROM_ENVYML,
        label="Generate from environment.yml",
        docker_url=docker_url,
        singularity_url=singularity_url,
        dockerfile_content=dockerfile_content,
        warnings=["Dockerfile generated from environment.yml — review, build, and push"],
    )


def _detect_python_version(reqs_path: Path | None) -> str:
    if reqs_path is None:
        return "3.10"
    try:
        content = reqs_path.read_text()
        m = re.search(r"python_requires\s*[>=<]+\s*([0-9]+\.[0-9]+)", content)
        if m:
            return m.group(1)
    except OSError:
        pass
    return "3.10"


def _check_reqs(source: CodeSource, tool_name: str) -> ContainerOption:
    hint = source.container_hint
    python_version = _detect_python_version(hint.requirements_txt_path if hint else None)
    docker_url = f"{tool_name}:latest"
    singularity_url = f"docker://{docker_url}"
    dockerfile_content = _render_dockerfile("reqs", python_version=python_version)
    return ContainerOption(
        source=ContainerSource.GENERATED_FROM_REQS,
        label="Generate from requirements.txt",
        docker_url=docker_url,
        singularity_url=singularity_url,
        dockerfile_content=dockerfile_content,
        warnings=["Dockerfile generated from requirements.txt — review, build, and push"],
    )


def _convert_singularity_def(content: str) -> str:
    """Best-effort conversion of a Singularity .def file to Dockerfile content."""
    bootstrap = "docker"
    from_image = "ubuntu:22.04"
    for line in content.splitlines():
        ls = line.strip()
        if ls.lower().startswith("bootstrap:"):
            bootstrap = ls.split(":", 1)[1].strip().lower()
        elif ls.lower().startswith("from:"):
            from_image = ls.split(":", 1)[1].strip()

    base = from_image if bootstrap == "docker" else "ubuntu:22.04"
    lines = [f"FROM {base}"]

    in_post = False
    run_cmds: list[str] = []
    for line in content.splitlines():
        if line.strip() == "%post":
            in_post = True
            continue
        if line.startswith("%") and line.strip() != "%post":
            in_post = False
        if in_post:
            cmd = line.strip()
            if cmd:
                run_cmds.append(cmd)

    if run_cmds:
        lines.append("RUN " + " && \\\n    ".join(run_cmds))

    return "\n".join(lines) + "\n"


def _check_singularity(source: CodeSource, tool_name: str) -> ContainerOption:
    hint = source.container_hint
    singularity_content = ""
    if hint and hint.singularity_def_path:
        try:
            singularity_content = hint.singularity_def_path.read_text()
        except OSError:
            pass

    dockerfile_content = _convert_singularity_def(singularity_content)
    docker_url = f"{tool_name}:latest"
    singularity_url = f"docker://{docker_url}"
    return ContainerOption(
        source=ContainerSource.CONVERTED_FROM_SINGULARITY,
        label="Convert from Singularity.def",
        docker_url=docker_url,
        singularity_url=singularity_url,
        dockerfile_content=dockerfile_content,
        warnings=[
            "Converted from Singularity.def — manual review strongly recommended",
            "%environment and %runscript sections not automatically converted",
        ],
    )


def _check_biocontainers(tool_name: str) -> ContainerOption | None:
    """Look up tool_name in BioContainers/Bioconda. Result is cached per tool_name."""
    if tool_name in _biocontainers_cache:
        return _biocontainers_cache[tool_name]

    try:
        resp = httpx.get(
            f"https://api.anaconda.org/package/bioconda/{tool_name}",
            timeout=10,
            follow_redirects=True,
        )
    except httpx.TimeoutException:
        _biocontainers_cache[tool_name] = None
        return None

    if resp.status_code != 200:
        _biocontainers_cache[tool_name] = None
        return None

    data = resp.json()
    version = str(data.get("latest_version", "latest"))
    build = "pyhdfd78af_0"
    s = get_standards()
    docker_url = f"{s.docker_registry}{tool_name}:{version}--{build}"
    singularity_url = f"{s.singularity_registry}{tool_name}:{version}--{build}"
    opt = ContainerOption(
        source=ContainerSource.BIOCONTAINERS,
        label="BioContainers",
        docker_url=docker_url,
        singularity_url=singularity_url,
    )
    _biocontainers_cache[tool_name] = opt
    return opt


def _make_stub(tool_name: str) -> ContainerOption:
    return ContainerOption(
        source=ContainerSource.STUB,
        label="Stub (fill in manually)",
        docker_url=f"{get_standards().docker_registry}{tool_name}:TODO--TODO",
        singularity_url=(
            f"{get_standards().singularity_registry}{tool_name}:TODO--TODO"
        ),
        warnings=[
            "No container — options:\n"
            "  1. Add a Dockerfile to your repo and re-run\n"
            "  2. Submit tool to Bioconda: "
            "https://bioconda.github.io/contributor/guidelines.html\n"
            "  3. Fill in container URLs manually before nf-core submission"
        ],
    )


# ── Default selection ──────────────────────────────────────────────────────────


def _set_default(options: list[ContainerOption], tier: int) -> None:
    """Set is_default=True on exactly one option using tier-aware priority."""

    def _first(src: ContainerSource) -> ContainerOption | None:
        return next((o for o in options if o.source == src), None)

    if tier <= 2:
        priority = [
            ContainerSource.BIOCONTAINERS,
            ContainerSource.DOCKERFILE,
            ContainerSource.GENERATED_FROM_ENVYML,
            ContainerSource.GENERATED_FROM_REQS,
            ContainerSource.CONVERTED_FROM_SINGULARITY,
            ContainerSource.STUB,
        ]
    else:
        priority = [
            ContainerSource.DOCKERFILE,
            ContainerSource.BIOCONTAINERS,
            ContainerSource.GENERATED_FROM_ENVYML,
            ContainerSource.GENERATED_FROM_REQS,
            ContainerSource.CONVERTED_FROM_SINGULARITY,
            ContainerSource.STUB,
        ]

    for src in priority:
        opt = _first(src)
        if opt is not None:
            opt.is_default = True
            return


# ── Public API ─────────────────────────────────────────────────────────────────


def discover(source: CodeSource, tool_name: str, tier: int) -> ContainerDiscovery:
    """Discover all available container options for a tool.

    Runs ALL checks — never short-circuits. Returns a ContainerDiscovery
    with every found option plus a STUB fallback, ordered with the default first.
    """
    options: list[ContainerOption] = []
    hint = source.container_hint

    # Check 1: Dockerfile in repo
    if hint and hint.has_dockerfile:
        options.append(_check_dockerfile(source, tool_name))

    # Check 2: environment.yml
    if hint and hint.has_environment_yml:
        options.append(_check_envyml(source, tool_name))

    # Check 3: requirements.txt
    if hint and hint.has_requirements_txt:
        options.append(_check_reqs(source, tool_name))

    # Check 4: Singularity.def
    if hint and hint.has_singularity_def:
        options.append(_check_singularity(source, tool_name))

    # Check 5: BioContainers (always run regardless of other findings)
    bio = _check_biocontainers(tool_name)
    if bio is not None:
        options.append(bio)

    # Check 6: Stub (always present)
    options.append(_make_stub(tool_name))

    # Apply tier-aware default
    _set_default(options, tier)

    # Order: default first, then the rest in discovery order
    default = next(o for o in options if o.is_default)
    rest = [o for o in options if not o.is_default]
    return ContainerDiscovery(options=[default] + rest)


def discover_sync(source: CodeSource, tool_name: str, tier: int) -> ContainerDiscovery:
    """Synchronous alias for discover() (discover is already synchronous)."""
    return discover(source, tool_name, tier)


# ── Selection phase ────────────────────────────────────────────────────────────

_FLAG_TO_SOURCES: dict[str, list[ContainerSource]] = {
    "dockerfile": [ContainerSource.DOCKERFILE],
    "biocontainers": [ContainerSource.BIOCONTAINERS],
    "generate": [ContainerSource.GENERATED_FROM_ENVYML, ContainerSource.GENERATED_FROM_REQS],
    "stub": [ContainerSource.STUB],
}


def _print_menu(discovery: ContainerDiscovery, console: Console) -> None:
    for i, opt in enumerate(discovery.options, start=1):
        star = "★" if opt.is_default else " "
        console.print(f"  [{i}] {star} {opt.label}")
        console.print(f"        {opt.docker_url[:55]}")
        if opt.warnings:
            console.print(f"        ⚠ {opt.warnings[0]}")


def _confirm(opt: ContainerOption, console: Console) -> None:
    console.print(f"Selected: {opt.label} → {opt.docker_url}")
    for warning in opt.warnings:
        console.print(f"⚠ {warning}")


def select(
    discovery: ContainerDiscovery,
    container_flag: str | None,
    no_interaction: bool,
    console: Console,
) -> ContainerOption:
    """Phase 2: choose one ContainerOption from a ContainerDiscovery.

    Priority:
      1. --container flag → find matching option (warn + fall-through if absent)
      2. --no-interaction or non-TTY → return default (options[0])
      3. TTY → show interactive menu, prompt for choice
    """
    # ── Case 1: explicit --container flag ─────────────────────────────────────
    if container_flag is not None:
        sources = _FLAG_TO_SOURCES.get(container_flag, [])
        matched = next((o for o in discovery.options if o.source in sources), None)
        if matched is not None:
            _confirm(matched, console)
            discovery.selected = matched
            return matched
        # Requested option not found — warn and fall through
        available = ", ".join(o.source.value for o in discovery.options)
        console.print(
            f"[yellow]Warning: --container {container_flag!r} not found. "
            f"Available: {available}[/yellow]"
        )

    # ── Case 2: non-interactive ────────────────────────────────────────────────
    is_tty = sys.stdin.isatty()
    if no_interaction or not is_tty:
        chosen = discovery.options[0]
        _confirm(chosen, console)
        discovery.selected = chosen
        return chosen

    # ── Case 3: interactive ────────────────────────────────────────────────────
    if len(discovery.options) <= 1:
        chosen = discovery.options[0]
        _confirm(chosen, console)
        discovery.selected = chosen
        return chosen

    default_opt = next(o for o in discovery.options if o.is_default)
    default_idx = discovery.options.index(default_opt) + 1

    _print_menu(discovery, console)

    chosen = default_opt
    for attempt in range(2):
        raw = input(f"Select [{default_idx}]: ").strip()
        if raw == "":
            chosen = default_opt
            break
        try:
            idx = int(raw)
            if 1 <= idx <= len(discovery.options):
                chosen = discovery.options[idx - 1]
                break
        except ValueError:
            pass
        # Invalid input on this attempt
        if attempt == 0:
            console.print("[yellow]Invalid selection — please enter a number from the list.[/yellow]")
        else:
            chosen = default_opt

    _confirm(chosen, console)
    discovery.selected = chosen
    return chosen


def resolve(
    source: CodeSource,
    tool_name: str,
    tier: int,
    container_flag: str | None,
    no_interaction: bool,
    console: Console,
) -> ContainerOption:
    """Run discover_sync then select. One-call interface for cli.py."""
    discovery = discover_sync(source, tool_name, tier)
    return select(discovery, container_flag, no_interaction, console)
