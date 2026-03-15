from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

# ── Container models ───────────────────────────────────────────────────────────


class ContainerSource(str, Enum):
    DOCKERFILE = "dockerfile"
    GENERATED_FROM_ENVYML = "generated_from_envyml"
    GENERATED_FROM_REQS = "generated_from_reqs"
    CONVERTED_FROM_SINGULARITY = "converted_from_singularity"
    BIOCONTAINERS = "biocontainers"
    STUB = "stub"


class ContainerOption(BaseModel):
    source: ContainerSource
    label: str
    docker_url: str
    singularity_url: str
    dockerfile_content: str | None = None
    warnings: list[str] = []
    is_default: bool = False


class ContainerDiscovery(BaseModel):
    options: list[ContainerOption] = []
    selected: ContainerOption | None = None


class ContainerHint(BaseModel):
    has_dockerfile: bool = False
    dockerfile_path: Path | None = None
    has_environment_yml: bool = False
    environment_yml_path: Path | None = None
    has_requirements_txt: bool = False
    requirements_txt_path: Path | None = None
    has_singularity_def: bool = False
    singularity_def_path: Path | None = None
    base_image: str | None = None
    conda_packages: list[str] = []
    pip_packages: list[str] = []


# ── Source / ingestion models ──────────────────────────────────────────────────


class RepoFile(BaseModel):
    path: Path
    language: str
    size_bytes: int
    is_likely_entrypoint: bool


class DocSource(BaseModel):
    url: str | None = None
    path: Path | None = None
    content: str
    source_type: Literal["url", "file"]
    fetch_error: str | None = None


class ExistingModule(BaseModel):
    path: Path
    tool_name: str
    subcommand: str | None = None
    process_name: str
    container_docker: str
    container_singularity: str
    label: str
    inputs: list[str] = []
    outputs: list[str] = []
    load_error: str | None = None


class CodeSource(BaseModel):
    source_type: Literal["file", "git", "directory"]
    path: Path | None = None
    url: str | None = None
    language: str
    raw_code: str
    filename: str
    repo_root: Path | None = None
    container_hint: ContainerHint | None = None
    repo_manifest: list[RepoFile] = []
    doc_sources: list[DocSource] = []
    existing_modules: list[ExistingModule] = []


# ── Discovery models ───────────────────────────────────────────────────────────


class DetectionMethod(str, Enum):
    CONSOLE_SCRIPTS = "console_scripts"
    CLICK_DECORATOR = "click_decorator"
    ARGPARSE_SUBPARSER = "argparse_subparser"
    SHELL_CASE_STATEMENT = "shell_case_statement"
    MULTI_SCRIPT_REPO = "multi_script_repo"
    LLM_INFERENCE = "llm_inference"
    SINGLE_SCRIPT = "single_script"


class FunctionalitySpec(BaseModel):
    name: str
    display_name: str
    description: str
    detection_method: DetectionMethod
    confidence: float
    code_section: str
    entry_point: str | None = None
    inferred_inputs: list[str] = []
    inferred_outputs: list[str] = []
    warnings: list[str] = []
    pre_selected: bool = False


class DiscoveryResult(BaseModel):
    source: CodeSource
    functionalities: list[FunctionalitySpec] = []
    selected: list[FunctionalitySpec] = []
    detection_method_used: DetectionMethod
    is_single_functionality: bool = False


# ── Module / channel models ────────────────────────────────────────────────────


class ChannelSpec(BaseModel):
    name: str
    type: Literal["file", "val", "map"]
    description: str
    pattern: str | None = None
    optional: bool = False
    format_tags: list[str] = []
    test_data_strategy: Literal["standard_format", "generatable", "custom", "unknown"] = "unknown"


class ModuleSpec(BaseModel):
    tool_name: str
    process_name: str
    functionality_name: str
    inputs: list[ChannelSpec] = []
    outputs: list[ChannelSpec] = []
    container_docker: str
    container_singularity: str
    container_source: ContainerSource
    dockerfile_content: str | None = None
    conda_package: str = ""
    environment_yml_content: str | None = None
    label: str
    ext_args: str
    script_template: str = ""
    tier: int
    confidence: float
    warnings: list[str] = []
    infer_failed: bool = False

    @classmethod
    def tier5_stub(cls, func_name: str, *, infer_failed: bool = False) -> ModuleSpec:
        return cls(
            tool_name=func_name.lower(),
            process_name=func_name.upper().replace("-", "_"),
            functionality_name=func_name,
            container_docker="",
            container_singularity="",
            container_source=ContainerSource.STUB,
            label="process_single",
            ext_args="def args = task.ext.args ?: ''",
            tier=5,
            confidence=0.0,
            warnings=[f"Tier 5: could not infer module spec for {func_name}"],
            infer_failed=infer_failed,
        )


# ── Validation / Fix / Review models ──────────────────────────────────────────


class FailureClass(str, Enum):
    CLASS_A = "class_a"
    CLASS_B = "class_b"
    CLASS_C = "class_c"


class FixSource(str, Enum):
    RULE = "rule"
    LLM = "llm"


class LintFailure(BaseModel):
    code: str
    message: str
    file: str
    line: int | None = None
    failure_class: FailureClass


class NfTestFailure(BaseModel):
    test_name: str
    error_type: Literal["file_not_found", "snapshot_mismatch", "process_failed", "unknown"]
    message: str
    failure_class: FailureClass


class TestReport(BaseModel):
    module_path: Path
    lint_passed: bool
    nftest_passed: bool
    lint_failures: list[LintFailure] = []
    nftest_failures: list[NfTestFailure] = []
    class_a_count: int = 0
    class_b_count: int = 0
    class_c_count: int = 0
    raw_lint_output: str = ""
    raw_nftest_output: str = ""


class ProposedFix(BaseModel):
    failure: LintFailure | NfTestFailure
    fix_source: FixSource
    description: str
    file_path: Path
    unified_diff: str
    approved: bool = False


class ReviewSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ReviewItem(BaseModel):
    severity: ReviewSeverity
    category: str
    message: str
    file: str
    line: int | None = None
    suggestion: str | None = None


class ReviewReport(BaseModel):
    module_path: Path
    items: list[ReviewItem] = []
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    submission_ready: bool = False


# ── Test data models ───────────────────────────────────────────────────────────


class TestDataSource(str, Enum):
    NFCORE_DATASETS = "nfcore_datasets"
    DERIVED = "derived"
    CHAINED = "chained"
    STUB = "stub"


class TestCase(BaseModel):
    name: str
    input_files: list[str] = []
    input_sources: list[TestDataSource] = []
    setup_module: str | None = None
    setup_module_process_name: str | None = None
    setup_module_input: str | None = None
    setup_module_output: str | None = None
    params: dict = {}
    expected_outputs: list[str] = []
    is_stub_test: bool = False
    derivation_script: str | None = None


class TestSpec(BaseModel):
    process_name: str
    test_cases: list[TestCase] = []
    needs_derivation: bool = False
    derivation_script_content: str | None = None
