# code-to-module

Convert scripts and Git repositories into submission-ready nf-core modules.

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Licence: MIT](https://img.shields.io/badge/licence-MIT-green)
![CI](https://github.com/your-org/code-to-module/actions/workflows/ci.yml/badge.svg)

## Overview

Contributing a module to nf-core means writing `main.nf`, `meta.yml`, `environment.yml`,
and a working nf-test — then getting them through lint and review. For well-known tools
with a Bioconda package, `nf-core modules create` gives you a starting point. For
everything else — custom scripts, in-house tools, software not yet in Bioconda — you
start from a blank file and figure out the channel structure, container URL, test data
strategy, and meta.yml fields by reading existing modules.

`code-to-module` automates the blank-file problem. Point it at a script or Git
repository and it generates a complete nf-core module directory: correct channel
structure, BioContainers lookup, nf-test with real or derived test data, and a review
report flagging anything that needs human attention before submission.

A note on what "automated" means here: the tool is LLM-assisted, not fully autonomous.
Claude handles inference — reading source code and documentation to determine channel
names, types, and the shell invocation — but everything around it is deterministic.
Discovery is rule-based, container resolution queries real APIs, and generation uses
fixed Jinja2 templates. The output is a best-effort module that the author should
review before submitting.

`code-to-module` is designed to sit alongside existing tooling, not replace it.
`nf-core modules create` scaffolds the files; this tool fills in the content.
[Seqera AI](https://seqera.io/ai) works at the pipeline level; the two complement
each other naturally. See [docs/development-tutorial.md](docs/development-tutorial.md)
for a detailed architectural walkthrough and the reasoning behind each design decision.

## Quick start

```bash
pip install code-to-module
export ANTHROPIC_API_KEY=sk-ant-...

code-to-module convert https://github.com/Teichlab/celltypist \
  --outdir modules/ \
  --no-interaction
```

Truncated terminal output from a confirmed run against Celltypist:

```text
→ Single functionality: celltypist
Tier 2  Confidence: 89%
Selected: BioContainers → quay.io/biocontainers/celltypist:1.7.1--pyhdfd78af_0
→ CELLTYPIST: 6 inputs, 5 outputs
✓ Quick lint passed
```

Then run a static review of the generated module before submitting:

```bash
validate-module review modules/celltypist/
```

Pass `--docs` to give the LLM more context when the source code alone is ambiguous:

```bash
code-to-module convert https://github.com/Teichlab/celltypist \
  --outdir modules/ \
  --docs https://celltypist.readthedocs.io/en/latest/ \
  --no-interaction
```

## What gets generated

For each detected functionality, `code-to-module` writes a directory under `--outdir`:

```text
modules/
└── celltypist/
    ├── main.nf                  # Process definition with inferred channels and script block
    ├── meta.yml                 # Module metadata: inputs, outputs, tool info, EDAM terms
    ├── environment.yml          # Conda environment pinned to resolved container version
    └── tests/
        ├── main.nf.test         # nf-test spec with test data paths or stub strategy
        └── nextflow.config      # ext.args wiring and test configuration
```

Optional additional files, written when applicable:

- `Dockerfile` — when the container is generated from `environment.yml` or `requirements.txt`
- `derive_test_data.sh` — when Strategy 2a applies: test data must be derived from an
  existing nf-core/test-datasets file and PR'd separately

## Complexity tiers

The tool assigns a tier to each detected functionality before generating anything. Tier
affects the default container strategy and how much of the module can be filled in
automatically.

| Tier | Description | Default container |
|------|-------------|-------------------|
| 1 | Known CLI tool, Bioconda entry exists, single clean I/O | BioContainers |
| 2 | Known tool, minor ambiguity (multi-output, optional params) | BioContainers |
| 3 | Custom script, inferrable I/O, no community container | Dockerfile or generated |
| 4 | Complex or ambiguous code — partial generation with TODOs | Dockerfile, generated, or stub |
| 5 | Cannot auto-generate — report only, manual creation required | N/A |

For Tier 1–2 tools, BioContainers is preferred over a repo-local `Dockerfile` because
the community-maintained image is more reproducible. For Tier 3–5 tools, a `Dockerfile`
found in the repo ranks first; if none is present, one is generated from `environment.yml`
or `requirements.txt`; if neither is present, a stub with TODOs is written.

Use `code-to-module assess-only` to see the tier assignment without generating anything:

```bash
code-to-module assess-only https://github.com/Teichlab/celltypist --json
```

## Container strategies

The `--container` flag overrides the tier-aware default at any time.

| Flag value      | Behaviour |
|-----------------|-----------|
| `dockerfile`    | Use a `Dockerfile` found in the repo |
| `biocontainers` | Use BioContainers (tool must be in Bioconda) |
| `generate`      | Generate a `Dockerfile` from `environment.yml` or `requirements.txt` |
| `stub`          | Write `TODO` placeholders — fill in manually |
| *(omitted)*     | Interactive menu on a TTY; tier-aware default on non-TTY or with `--no-interaction` |

Inspect all available container options without generating anything:

```bash
code-to-module containers https://github.com/lab/tool
code-to-module containers https://github.com/lab/tool --functionality align --json
```

## validate-module

`validate-module` is a separate entry point for module validation. It can be used on
modules generated by `code-to-module` or on any existing nf-core module directory.

**test** runs `nf-core modules lint` and `nf-test` against the module, captures
structured output, and classifies each failure:

```bash
validate-module test modules/celltypist/
validate-module test modules/celltypist/ --lint-only
validate-module test modules/celltypist/ --json-output report.json
```

**fix** loads a test report (or re-runs validation if none is provided), shows a
coloured diff for each fixable failure, and waits for explicit approval before writing
any file. Every diff panel is labelled with the fix source so you know what to trust:

```bash
validate-module fix modules/celltypist/
validate-module fix modules/celltypist/ --from-report report.json --class-a-only
validate-module fix modules/celltypist/ -y   # auto-approve Class A only; Class B still prompts
```

At each diff you choose: `y` apply, `n` skip, `a` apply all remaining, `s` skip all
remaining, `q` quit. Validation re-runs automatically after fixes are applied.

Fix classes reflect how much trust you should place in each proposal:

| Class | Label | Trigger condition | Auto-fixable |
|-------|-------|-------------------|--------------|
| A | `[rule]` | Deterministic structural issue: wrong emit name, missing topic channel tag, container URL format, ext.args pattern, meta.yml field | Yes (with approval) |
| B | `[llm]` | Requires reading code in context: wrong output glob, wrong process label, unclear channel description | Yes (with approval; lower trust signal) |
| C | `[manual]` | Structural ambiguity the tool cannot resolve safely | Never — explains what to do and stops |

**review** performs static analysis only — no subprocess, no test execution. It checks
channel naming conventions, process label appropriateness, `ext.args` usage, meta.yml
completeness, versions channel structure, and EDAM ontology coverage. Output is a
`ReviewReport` with three severity levels:

```text
CELLTYPIST — review
─────────────────────────────────────────────────────────────────────
  ERROR    versions channel missing 'topic: versions' tag
  WARNING  process label 'process_medium' may be low for h5ad inputs
  INFO     EDAM term not found for .pkl input — add manually if known
─────────────────────────────────────────────────────────────────────
  1 error (blocks submission) · 1 warning · 1 info
```

`ERROR` items block nf-core submission. `WARNING` items should be fixed before
opening a PR. `INFO` items are suggestions. Pass `--errors-only` to suppress the
lower-severity rows.

```bash
validate-module review modules/celltypist/
validate-module review modules/celltypist/ --errors-only --json-output review.json
```

## Known limitations

**Tier 4–5 tools.** Perl wrappers (TrimGalore, for example), tools with no detectable
CLI structure, and tools that require a running database to operate correctly assess as
Tier 4 or Tier 5. The tool reports why it cannot proceed and suggests a manual
approach. This is correct behaviour — attempting partial generation for these tools
produces modules that mislead more than they help.

**Library-only tools are not supported.** Tools whose primary public interface is a
Python API rather than a CLI command (importable functions, no `console_scripts` entry
point or argparse/Click CLI) are outside the current scope. If a library has a
thin CLI wrapper, that wrapper is used; if not, generation stops at discovery.
A `library-to-module` companion that generates a CLI wrapper first is planned but
not yet implemented.

**Domain-specific test data falls back to stub.** Input formats that are not in
nf-core/test-datasets — `.h5ad`, `.pkl`, `.mzML`, large reference databases — produce
`stub` strategy test data. The generated `tests/main.nf.test` is syntactically valid
and will pass lint, but it cannot test real data flow. The TODO comments in the file
describe what test data would be needed for a full nf-test run. This is expected
behaviour, not a defect.

**LLM non-determinism.** Two runs against the same source may produce slightly
different channel names, output glob patterns, or process labels. The structural
invariants (meta as first input, versions channel, ext.args wiring) are enforced
post-processing and will always be present, but the inferred content around them is
best-effort. Always review the generated module before submitting.

**The tool does not submit pull requests.** `code-to-module` generates and validates;
you review and submit. nf-core module PRs require human sign-off on test data
provenance, licence compatibility, and tool-specific correctness that the tool cannot
assess automatically.

## Requirements

**Runtime dependencies** are installed automatically with `pip install code-to-module`:
Python 3.10+, `anthropic>=0.30`, `click>=8.1`, `gitpython>=3.1`, `httpx>=0.27`,
`jinja2>=3.1`, `nf-core>=2.14`, `pydantic>=2.0`, `rich>=13.0`, `ruamel.yaml>=0.18`.

**External tools** required for full validation (not installed by pip):

- `nf-test` — for `validate-module test` and `validate-module fix`
- Docker or Singularity — needed by `nf-test` to run process tests

**API key:** set `ANTHROPIC_API_KEY` in your environment before running `convert`. The
`assess-only`, `containers`, `review`, and `update-standards` commands do not call the
API.

**Devcontainer:** a `.devcontainer/devcontainer.json` is included with all Python
dependencies, nf-core/tools, and nf-test pre-installed. Open the repo in VS Code or
GitHub Codespaces and everything is ready without a local install.

## Contributing

**Contributing to code-to-module:** fork the repo, install with `pip install -e ".[dev]"`,
and run `pytest -x -q -m "not network and not llm"` before opening a PR. The standards
schema at `src/code_to_module/standards/data/nf_core_standards.json` is the canonical
source for all nf-core conventions — if a convention changes, update the schema first,
then update any code or template that reads from it. Never hardcode nf-core conventions
directly in Python or Jinja2 templates. See
[docs/development-tutorial.md](docs/development-tutorial.md) for the full architectural
context and the reasoning behind design decisions.

**Contributing generated modules to nf-core:** `code-to-module` gets you to a
reviewable draft; the nf-core contribution guide covers the rest — test data
requirements, PR checklist, and review process. See the
[nf-core modules contributing guide](https://nf-co.re/docs/contributing/modules).

## Licence

MIT
