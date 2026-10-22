<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Find local Harbor tasks and NeMo Gym workloads

Use this file-only search when the user wants to find existing evaluations or
when an authorized workflow needs local source candidates. It needs Python 3.10+
and no provider packages, credentials, model calls, Docker, or runtime setup.
The script never loads YAML/TOML, imports repository code, follows dataset
references, or executes provider commands. A candidate is not a validated task.

`<skill_dir>` below is the installed **eval-author** skill directory:

```bash
python3 <skill_dir>/scripts/search_tasks.py --repo /absolute/project
python3 <skill_dir>/scripts/search_tasks.py --repo /absolute/project --provider harbor
python3 <skill_dir>/scripts/search_tasks.py --repo /absolute/project --provider gym --query math
```

The JSON records candidate paths, observed marker files, provider/layout hints,
and search scope. Every candidate has `validation: "not_checked"`. Directory
paths identify Harbor and NeMo Gym extension tasks; file paths identify NeMo Gym manifests
and config candidates. `.` identifies a task at the supplied root. `--query` is a
case-insensitive substring filter on the reported path, not semantic search.
Filters apply after the same bounded traversal; they do not reduce its scope.

## Matching rules

| Layout evidence | Candidate | Interpretation |
| --- | --- | --- |
| `task.toml` in any scanned directory, including the root | Harbor task | Include incomplete scaffolds; other markers are evidence, not validation requirements. |
| `tasks.jsonl` and `instruction.template.md` together | NeMo Gym parameterized task | Rows and template are not parsed or expanded. |
| `task.toml` and `tests/verifier.py` together | NeMo Gym Python-verifier task | A NeMo Gym extension of the Harbor layout. Parameterized evidence takes precedence when both are present. |
| `manifest.yaml` | NeMo Gym manifest candidate | Retain even outside a catalog tree, where it may be an example or unrelated manifest. |
| `environments/<name>/config.yaml` | NeMo Gym legacy config candidate | Structural match only. |
| `benchmarks/**/*.yaml` | NeMo Gym legacy config candidate | Native NeMo Gym checks config contents; this search deliberately leaves that unverified. |
| `resources_servers/<name>/configs/*.yaml` | NeMo Gym resource config candidate | Some contain workloads; others only configure a component. Do not call every match a task. |

## Harbor environment and step evidence

Harbor candidates still use `task.toml` as the root marker. A multi-step task is
found even without a root `instruction.md` or `tests/test.sh`. Dockerfiles,
`steps/` directories, and verifier scripts alone do not establish a task root.

For each task, evidence also includes observed environment and separate-verifier
build files (`Dockerfile`, `docker-compose.yaml`, `docker-compose.yml`, and
`Apptainer.def` under `environment/` or `tests/`) and `solution/solve.sh` / `solution/solve.bat`. These are
supporting paths, not a declaration that every backend accepts every filename.
No environment file is required: a task may use a prebuilt image configured in
`task.toml`. See Harbor's [environment documentation](https://docs.harborframework.com/tasks/environment).

The documented verifier entrypoints are `tests/test.sh` and `tests/test.bat`.
We additionally retain `verifier.sh` at the task root or in `tests/` as supporting
evidence when present, without treating it as an automatic Harbor entrypoint.
Separate verifier builds can supply the entrypoint inside their image, so missing
local scripts do not establish invalidity. See the [verifier documentation](https://docs.harborframework.com/tasks/verifier).

Recognized files beneath `steps/<name>/` contribute to the parent task's evidence:
`instruction.md`, verifier scripts and build files in `tests/`,
`solution/solve.sh` / `solution/solve.bat`, and `workdir/setup.sh`. Shared
`tests/helpers.py` files are also recorded. `observed_steps` lists their directories in sorted path
order. It does not infer declared steps, execution order, or validity from these
files; those come from configuration. A step may use the task-level verifier.
See Harbor's [multi-step format](https://docs.harborframework.com/tasks/multi-step)
and [task overview](https://docs.harborframework.com/tasks/overview).

All marker paths come from the bounded scan, including step evidence. A nested
`task.toml` starts a separate candidate whose files belong to that nearest root.
Excluded symlinks and unscanned descendants cannot supply supporting evidence.

The search host and task OS are independent. macOS and Linux hosts can discover
Windows `.bat` files, and Windows hosts can discover `.sh` files; the scanner
never executes them. Harbor selects the executable extension from the task's
configured environment OS, as described in its [solution documentation](https://docs.harborframework.com/tasks/solution).
The task-search CI job runs these layout tests on Linux, macOS, and Windows with
only pytest installed. Windows symlink tests explicitly skip if the runner lacks
symlink creation privilege; other errors remain failures.

## NeMo Gym catalog context

The nearest scanned `environments/` or `benchmarks/` ancestor supplies catalog
context, including when that directory is the explicit search root. Nested
projects remain separate paths; names are not merged. For legacy configs, supply
the repository or catalog-tree root: searching directly inside
`environments/<name>` omits the identifying ancestor and will not classify a
standalone `config.yaml`. Explicit roots do not inspect their parents. A manifest's sibling
`config.yaml` is grouped into its evidence rather than emitted twice. Other
benchmark config variants remain separate candidates. Environment tombstones
named `.nemo_gym_tombstone` are reported when observed; the candidate remains
visible for explanation rather than being silently dropped.

These rules are grounded in [Harbor 0.20.0 task paths](https://github.com/harbor-framework/harbor/blob/v0.20.0/src/harbor/models/task/paths.py)
and the pinned NeMo Gym source used by this repository:
[registry](https://github.com/NVIDIA-NeMo/Gym/blob/3045a793346a31291d7ea4ae6af3f94a35036ce5/nemo_gym/registry.py),
[benchmark discovery](https://github.com/NVIDIA-NeMo/Gym/blob/3045a793346a31291d7ea4ae6af3f94a35036ce5/nemo_gym/benchmarks.py).
The two extension markers preserve Eval Author's existing
[NeMo Gym classification](../../eval-author-discover/scripts/providers/gym/_explore.py);
they do not establish support for execution by the pinned NeMo Gym runtime.
The search is a conservative inventory of these layouts, not a replacement for
native validation or NeMo Gym's catalog resolver.

NeMo Gym's native catalog applies manifest validation, content checks for legacy
workloads, tombstones, and precedence across extra roots, working directory,
and installation. Its `gym list environments --json` and `gym search` commands
serve that runtime-backed catalog; see the [NeMo Gym 0.6.0 CLI reference](https://docs.nvidia.com/nemo/gym/v0.6.0/reference/cli-commands/).
The local search does not borrow built-in catalog entries from an installation
or download Harbor registry datasets. It does not establish a usable native
name or execution command from a filename.

## Scope and completeness

The scanner visits sorted directory paths and emits candidates sorted by path,
provider, and kind. It reads directory metadata only. It has no timestamps or
runtime-dependent fields; for an unchanged tree, root, filters, and limits,
repeated reports are identical. Compare `repo` separately when relocating a
fixture. Concurrent filesystem edits are outside that guarantee.

Defaults are depth 12 below the supplied root and 50,000 retained directory
entries. Override with `--max-depth` and `--max-entries` when a known location
needs a larger scope. If a directory exceeds the remaining entry budget, its
entire partial listing is discarded and the scan stops with an incomplete
result. This avoids choosing arbitrary files based on filesystem listing order.
The scanner may inspect one extra entry to detect that limit. Files already
found in completed directories remain in the result.

The fixed descendant exclusions are `.git`, `.venv`, `venv`, `node_modules`,
`__pycache__`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, and `.tox`. Symlinks
are not followed, including marker files. An explicitly supplied root is
resolved and scanned even if its name is normally excluded. `.eval-author/`
is included because it can contain authored tasks. Git ignore rules are not
applied; ignored fixtures or generated results may therefore appear as candidates.
Templates and examples also remain candidates and need source interpretation.

`search` lists actual exclusions, filesystem errors, reached limits, and the
number of fully scanned directories and retained entries. `complete: true`
means traversal completed within the declared exclusions. It does not cover
symlink targets, remote data, nonstandard layouts, or other evaluation formats.
Missing markers in an incomplete scan may simply be outside the scanned scope.

Exit codes are 0 for completed inventory, 1 for incomplete inventory, and 2 for
invalid arguments or an unusable root. An empty completed inventory exits 0;
that is not a readiness pass or proof that the repository contains no evals.

## Return the findings

Preserve this evidence with the discovery findings. Read representative task
instructions, suite READMEs, runners, and CI commands to explain purpose and
source-derived run instructions. Continue bounded inspection for Harbor job
configs, nonstandard NeMo Gym configs, scripts, notebooks, and manual rubrics; those
are outside this layout search. Do not fabricate missing execution commands or
infer readiness from marker files.

State the searched root, filters, exclusions, incomplete conditions, and candidate
paths. Resolve missing locations or scope before claiming absence. Finding
candidates does not authorize provider probing, installation, validation,
execution, auditing, or task creation; continue according to the user's request
and the calling flow's handoff.

## Sharing with readiness discovery

Discover imports this same helper to enumerate task roots and records the complete
inventory in `task_search` in its JSON report, including environment, solution,
verifier, and step evidence. Its existing generated-directory exclusions are
passed to the helper and reported in `search.excluded_directory_names`.
Readiness preserves its previous scope: it excludes the repository root itself
and `task_template` roots and still requires `task.toml`. These roots and NeMo Gym
parameterized scaffolds without TOML remain inventory candidates only.

Full discovery has no task depth or entry limit by default (reported as JSON
`null`), preserving discovery of large and deeply nested suites. The optional
`--task-max-depth` and `--task-max-entries` flags impose limits explicitly.
An incomplete task scan still makes Explore invalid and prevents a repository
runnable verdict, including repositories containing NeMo Gym manifests: config
discovery alone cannot establish that all tasks were found. Fix unreadable paths
or remove/raise explicit limits before claiming repository-wide readiness.

Standalone search keeps its bounded defaults and narrower exclusions so that
potential source tasks in build/vendor/cache directories remain visible for
inspection. Full discovery retains its larger historical generated-directory
exclusion set; the report lists the exact selected scope.
Configuration parsing, dataset grouping, fingerprinting, and provider validation
remain discovery responsibilities; their traversal is not bounded by the task
search limits. File evidence never replaces native validation.

Both modes deliberately exclude symlink task markers as well as symlink
directories; unlike the previous readiness walker, a linked `task.toml` no
longer establishes a task. This keeps task evidence within the selected tree.
Windows directory reparse points (including junctions) are excluded; regular
reparse-point files such as cloud placeholders remain filename evidence.
Non-root directories containing a `.git` file or directory are excluded as
nested repositories before collecting their markers. Select such a repository
explicitly as `--repo` to search it. These policies apply to task enumeration,
not discovery's separate config/fingerprint traversal.

`search.limits_reached_count` records the total number of limit observations.
`search.limits_reached` contains at most the first 100 paths in sorted order;
a truncated sample never makes an incomplete search complete.
