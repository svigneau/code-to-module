# CLAUDE.md — code-to-module

## Build Order

Build prompts in document order (1 through 34). Two prompts write tests before
their implementation prompt: Prompt 5 (standards contract) and Prompt 12
(container contract) — do not skip ahead when their tests are red from ImportError
or AttributeError. That is expected and intentional: the tests define the contract,
the next prompt satisfies it.
Do not proceed to the next prompt until all tests for the current prompt are green.
Full prompt text is in docs/development-tutorial.md.

## Project Purpose

CLI tool that converts scripts/Git repos into submission-ready nf-core modules.
Uses LLM inference to determine process inputs, outputs, containers, and parameters.

## Architecture

- `ingest.py` — accepts local file paths, directories, or Git URLs; returns CodeSource with repo manifest
  Also accepts optional --docs list of URLs/paths; fetches content into CodeSource.doc_sources
  Also accepts optional --existing-modules list of module directory paths; parses into
  CodeSource.existing_modules for use by discover, infer, and generate
- `discover.py` — detects distinct functionalities using rules + LLM; interactive selection
  Rule-based detectors use code only; LLM fallback incorporates doc_sources content when present
- `assess.py` — assigns Tier 1-5 complexity per FunctionalitySpec (not per CodeSource)
- `infer.py` — calls Anthropic API scoped to one FunctionalitySpec's code section
- `container.py` — two-phase container handling: discover all options, then select one
- `bioconda.py` — check Bioconda for existing packages; generate meta.yaml scaffold for new submissions
- `standards/` — extractable subpackage; no imports from rest of code_to_module
  - `loader.py` — Standards class + get_standards() singleton
  - `data/nf_core_standards.json` — versioned schema; `data/STANDARDS_CHANGELOG.md`
  - Import as: `from code_to_module.standards import get_standards`
- `test_data_match.py` — Strategy 1: match channel specs to existing nf-core/test-datasets files
- `test_data_derive.py` — Strategy 2–4: derive test data from existing nf-core files,
  chain an upstream nf-core module in an nf-test setup block, or fall back to Nextflow stub mode
- `test_gen.py` — orchestrates test data strategy and produces TestSpec + derive_test_data.sh
- `generate.py` — renders Jinja2 templates into module files
- `quick_lint.py` — fast post-generate structural checks (no subprocess); feeds warnings to CLI output
- `validate.py` — runs nf-core lint + nf-test, captures output, returns structured TestReport
- `fix.py` — classifies failures, proposes rule-based or LLM-assisted fixes as diffs
- `review.py` — static analysis against nf-core style conventions, returns ReviewReport
- `validate_cli.py` — Click group for validate-module entry point (test/fix/review commands)
- `api.py` — clean public API for programmatic use by other tools; no Rich output

## Entry Points

Two CLI entry points ship in the same package:
code-to-module ← conversion pipeline (convert, assess-only, containers,
bioconda-recipe, update-standards)
validate-module ← module validation suite (test, fix, review)
Separating them means validate-module can be extracted into a standalone
`nf-module-tools` package later without renaming any user-facing commands.
validate_cli.py imports from validate.py, fix.py, review.py only.
It must NOT import from ingest, discover, assess, infer, container, or generate.

## Functionality Discovery Rules (CRITICAL)

Discovery runs BEFORE assess and infer. It determines how many modules to generate.

Rule-based detection (always run first, fast, no LLM):

- click/typer @command decorators → one FunctionalitySpec per decorated function
- argparse add_subparsers + add_parser calls → one per subparser name
- shell case statement dispatch → one per case branch that calls external tools
- multiple top-level scripts in repo (each _.py, _.sh, \*.R not named utils/helpers/common) →
  one FunctionalitySpec per script file

LLM-based detection (only when rule-based finds zero or one functionality):

- Send repo manifest + combined code to LLM
- Ask it to identify distinct independently-invokable operations
- Each LLM-detected functionality carries DetectionMethod.LLM and lower confidence
- If LLM also finds only one functionality: proceed as single-module case (no selection UI)

nf-core module count rules:

- One module per distinct, independently-invokable functionality
- Never merge two functionalities with different I/O into one module
- A single monolithic script with no subcommands = one module (do not artificially split)
- Low-confidence functionalities (< 0.50) shown in UI but not pre-selected

Selection UI:

- Always shown when ≥ 2 functionalities detected, regardless of confidence
- Pre-select all functionalities with confidence ≥ 0.70
- Low-confidence entries shown with ⚠ and not pre-selected
- --functionalities flag for non-interactive selection (comma-separated names)
- --all-functionalities flag to select all without UI

## nf-core Standards (CRITICAL)

Always follow nf-core/tools 3.5+ conventions:

- Use TOPIC CHANNELS for versions (not versions.yml file)
- Input channels: `tuple val(meta), path(files)` pattern
- Output channels: must include `versions` topic channel
- Labels: process_single / process_medium / process_high / process_high_memory
- Container: both Docker (quay.io/biocontainers) AND Singularity URLs required
- meta.yml: must include EDAM ontology terms where known
- All params set via ext.args in conf/modules.config, never hardcoded in module

## Container Handling (Two Phases)

### Phase 1 — Discovery (container.discover())

Run ALL checks in parallel, collect every available option as a ContainerOption.
Never stop early. Return a ContainerDiscovery with all options found.
Checks to run: Dockerfile in repo, environment.yml, requirements.txt,
Singularity.def, Bioconda/BioContainers lookup.

### Phase 2 — Selection (container.select())

Present options to the user and return their choice.
Default option priority order (used when user accepts default or --no-interaction):

1. Dockerfile in repo
2. BioContainers (if tool is Tier 1–2, i.e. known Bioconda package)
3. Generate from environment.yml
4. Generate from requirements.txt
5. Convert from Singularity.def
6. Stub

IMPORTANT: For Tier 1–2 tools (known Bioconda entry), BioContainers is ranked ABOVE
repo files in the default order — the community standard image is preferred for
well-known tools. For Tier 3–5 tools, Dockerfile/generated options rank first.

User can override default via:

- Interactive menu (TTY detected automatically)
- --container dockerfile|biocontainers|generate|stub flag
- --no-interaction flag (use default silently, no menu shown)

## Complexity Tiers

- Tier 1: Known tool, single I/O, Bioconda entry exists → full generation
- Tier 2: Known tool, minor ambiguity (multi-output, optional params) → generation with warnings
- Tier 3: Custom script, inferrable I/O, no container → generation + container from options
- Tier 4: Complex custom code, ambiguous channels, multiple tools → partial generation + TODOs
- Tier 5: Cannot proceed → report why, suggest manual approach

## Code Style

- Python 3.10+, type hints required on all functions
- Click for CLI (not argparse)
- Jinja2 for all template rendering
- Never hardcode tool names or container URLs — always infer or look up
- Pydantic v2 for all data models (CodeSource, ModuleSpec, TestSpec, ContainerOption,
  ContainerDiscovery)
- Rich for terminal output (progress bars, status panels, tier badges, selection menus)

## Testing

- pytest for unit tests
- Fixtures in tests/fixtures/
- Mock Anthropic API calls in tests — never make real API calls in CI
- Mock all httpx calls — never hit real APIs in CI
- Test discovery and selection phases independently
- Test Tier-aware default ordering explicitly

## When Adding New Templates

1. Add .j2 template to src/code_to_module/templates/
2. Add corresponding Pydantic model field if new data needed
3. Add test fixture that exercises the new template
4. Update meta.yml.j2 if new output type added

## Standards Schema (CRITICAL)

Never hardcode nf-core conventions directly in Python code or Jinja2 templates.
Always read them from code_to_module.standards (the standards/ subpackage),
which loads src/code_to_module/standards/data/nf_core_standards.json.
Import: from code_to_module.standards import get_standards

Examples of things that MUST come from the schema, not be hardcoded:

- Valid process labels (process_single, process_medium, etc.)
- Required output channel names (versions)
- Container registry base URLs
- Required meta.yml fields
- EDAM ontology term mappings
- Test data index (nf-core/test-datasets paths and metadata)
- Generation tool commands and templates

When writing any code that references an nf-core convention, ask:
"Is this in the schema?" If not, add it to nf_core_standards.json first,
then read it via get_standards() from code_to_module.standards.

## Fix Safety Rules (CRITICAL)

The fix command MUST follow these rules without exception:

1. NEVER modify files without showing a diff and receiving explicit human approval.
   Every proposed fix is shown as a coloured Rich diff panel before any write occurs.

2. The source of every proposed fix MUST be visible in the diff panel:
   "[rule] Missing ext.args pattern" vs "[llm] Output pattern corrected to \*.sorted.bam"
   Users need to know whether to trust a fix as deterministic or as a suggestion.

3. Rule-based fixes (Class A) ONLY for failures with deterministic structure:
   wrong emit name, missing topic channel tag, container URL format errors,
   missing ext.args, meta.yml field mismatches. Rules never call the LLM.

4. LLM-assisted fixes (Class B) ONLY when reading code in context is required:
   wrong output filename pattern, wrong process label, unclear channel description.
   LLM fixes are clearly labelled and carry a lower trust signal in the diff panel.

5. Class C failures are NEVER fixed automatically. The tool explains what it found,
   why it cannot fix it, and what the human needs to do. Then it stops.

6. After applying fixes, always re-run validation. Never claim "fixed" without verifying.

## Review Scope

The review command performs static analysis only — it does not run tests.
It checks nf-core style conventions: channel naming patterns, label appropriateness
for the inferred compute requirements, ext.args usage, meta.yml completeness,
and EDAM ontology coverage. It produces a ReviewReport with severity levels:
ERROR (blocks submission) | WARNING (should fix) | INFO (suggestion).
Test data selection follows a strict three-strategy priority order.
Never skip a strategy without trying it first:

Strategy 1 — Match existing nf-core/test-datasets (test_data_match.py)
Query the test_data.index in the standards schema.
Match on: format tag, organism preference, paired/single-end, size.
Always prefer the smallest file that satisfies the channel spec.

Strategy 2a — Derive from existing nf-core data (test_data_derive.py)
Only if Strategy 1 finds no match.
Subset or transform an existing nf-core/test-datasets file (VCF region, FASTA extract,
BAM subsample). Generates derive_test_data.sh. The output file must be PRed to
nf-core/test-datasets. Supported derivations in standards.derivation_templates.
Preferred over chain for most formats — a stored file keeps CI test runtime low.

Strategy 2b — Chain an upstream nf-core module (test_data_derive.py)
Some inputs are the natural output of a known upstream nf-core module.
Emit a setup {} block in main.nf.test — no stored file needed.
Known chain modules are in standards.chain_modules.
Use ONLY when: (a) the input file would be too large for nf-core/test-datasets,
OR (b) the upstream module runs extremely fast (e.g. index generation from a
small reference). nf-core discourages setup{} blocks as a general default because
they increase test runtime — derive is preferred when the file is storable.

Strategy 3 — Nextflow stub mode
Use when data is too large, proprietary, or cannot be derived from nf-core data.
Module includes a stub: block; nf-test runs with options "-stub".
Generate a commented TODO block in main.nf.test explaining what is needed.
Never silently produce an nf-test that will always fail.

Each ChannelSpec carries a test_data_strategy field set during LLM inference (Prompt 8).
The LLM should classify each input as: "standard_format" | "generatable" | "custom".
test_data_match.py and test_data_derive.py use this hint to route efficiently.

## Testing Protocol

Run tests in escalating stages. Never skip a stage to reach a later one.

### Stage 1 — After each prompt (fast, always)

Run pytest for the modules touched in this prompt only.
Fix any failures before proceeding. Do not run the full suite yet.

### Stage 2 — After a milestone or bug fix (full unit suite)

pytest -x -q -m "not network and not llm"
Stop on first failure (-x). Identify whether each failure is a regression
(was passing before this change) or pre-existing. Fix regressions before
proceeding; report pre-existing failures without fixing them.

### Stage 3 — After Prompt 22 (architectural integrity)

Run in this order, reporting results separately:
pytest tests/test_import_boundaries.py -v
pytest -x -q -m "not network and not llm"
pytest tests/test_chain.py -v
A boundary violation is a different class of problem from a unit failure —
do not conflate them.

### Stage 4 — Real-world integration (Celltypist)

code-to-module convert https://github.com/Teichlab/celltypist \
 --outdir /tmp/celltypist_test \
 --docs https://celltypist.readthedocs.io/en/latest/ \
 --no-interaction
Then verify every item in the Prompt 16 manual checkpoint in
docs/development-tutorial.md. Report each checkpoint item as PASS or FAIL.
Do not summarise — list every item explicitly.

### Stage 5 — Discovery robustness (network, slow)

pytest tests/test_discovery_robustness.py -v -m network
Report failures with full pytest output, not a summary. If a repo clone
fails due to a transient network error, retry once before reporting.

### Stage 6 — LLM contract tests (opt-in, costs API credits)

ANTHROPIC_API_KEY=<key> pytest tests/test_llm_contract.py -v -m llm
For each failing invariant, identify whether the failure is in the raw LLM
output (invariant never produced) or in the post-processing guard (invariant
produced but guard failed to enforce it). These require different fixes.

### Failure protocol — apply at every stage

1. Show the full pytest output for the failing test, not a paraphrase.
2. Identify the root cause before proposing a fix.
3. Confirm the fix is general — not a special-case patch for the failing test.
4. Re-run the failing test after the fix to verify it passes.
5. Re-run the full suite for the affected module to check for regressions.
   Never mark a task complete while any test in the relevant suite is red.

## AI Attribution Policy

This project was developed with significant assistance from Claude (Anthropic).
The acknowledgement lives in README.md and CONTRIBUTING.md — not in individual
commit metadata.

When making commits:

- Do NOT add Co-authored-by: Claude trailers to commit messages
- Do NOT add any AI co-author metadata to commits
- If a PR or commit was substantially AI-assisted, note it in the PR description
  in plain language (e.g. "Implementation generated by Claude Code from the spec
  in Prompt 12") — this is informative without creating legal ambiguity

The README acknowledgement covers all AI assistance across the project. Individual
commit attribution is not needed and pollutes the contributor graph.

## Common Pitfalls

- nf-core 3.5+ uses topic channels: `emit: versions, topic: 'versions'`
- Singularity URLs use `https://depot.galaxyproject.org/singularity/` prefix
- Docker URLs use `quay.io/biocontainers/` prefix
- Always quote container strings in main.nf (interpolation issues)
- meta.yml `tools` section requires homepage and documentation URLs
- Generated Dockerfiles must be pushed to a registry before nf-core submission
- Discovery runs ALL checks — do not short-circuit even if Dockerfile is found
- Selection applies Tier-aware default — check tier BEFORE setting is_default flags
- Never hardcode nf-core conventions in code — always read from standards/loader.py
- validate_cli.py must not import from ingest, discover, assess, infer, container,
  or generate — this boundary enables future extraction of the validation suite
- standards/ subpackage must not import from any sibling module in code_to_module
  (no imports from ingest, assess, container, etc.) — same extraction reason
