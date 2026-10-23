<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Runtime prerequisites

Read for the [shared authoring prerequisite checks](shared-prerequisites.md#authoring-checks)
or readiness validation. A file-only
Explore stays in the discovery skill and does not run these probes.

## Contents

- [Resolve and verify the runtime](#runtime-prerequisite-checks)
- [Check optional Harbor assistant skills](#check-for-optional-harbor-skills)

## Runtime prerequisite checks

Use the bundled locator for evaluation runtime prerequisite checks, for both providers.
It uses [runtime-policy.json](../scripts/runtime-policy.json) for supported package
ranges, Python requirements, and flow-specific import/CLI capabilities. It does
not install, activate, upgrade, scan evals, or execute jobs. It does execute
installed runtime probes, so inspect only trusted runtime locations.

```bash
python3.11 <skill_dir>/scripts/locate_runtime.py --repo /absolute/project --provider harbor --flow discovery
python3.11 <skill_dir>/scripts/locate_runtime.py --repo /absolute/project --provider gym --flow authoring
```

Use Python 3.11+ to run the locator. Select `discovery` for readiness and
`authoring` for creation/improvement prerequisites, first-eval, task-create,
environment, or trace-derived authoring. Audit shared entry checks the existing
Python environment and packages in `audit_tools` from the policy directly; it
does not probe an evaluation runner. After ATIF evidence is selected for audit
measurement, use `--provider harbor --flow audit` for the trajectory parser,
including for converted NeMo Gym evidence. This does not change the suite's framework
or require an evaluation environment. Use `--provider both` only
when both runtimes are needed. File-only inventory never runs this command.
Here `<skill_dir>` is the eval-author skill directory. The shared files ship in
its `scripts/` directory; every flow uses this one locator and policy. If this
shared skill is missing,
report the incomplete skill installation instead of inventing a probe sequence.

The JSON contains every candidate's Python, CLI, installed package/Python
versions, origins, checks, status, and failure reason. Search is bounded to:

- explicit paths and `[tool.eval-author.runtimes]` declarations;
- project `.venv` and `venv` environments;
- all matching executables on `PATH`, and existing
  environments under `uv tool dir` when uv exists.

The locator checks all these sources before making a selection. It never calls
`uv tool run` or crawls the home directory. Candidate count and total probe time
are bounded and reported. Incompleteness blocks selection only when it could
hide an equal- or higher-priority candidate for that provider. A failed global
uv search cannot override a compatible explicit interpreter. The search still
reports its incomplete state and affected priorities. Exit 0 means each
requested provider was selected, not that evals are ready. Exit 1 preserves
missing, broken, incompatible, ambiguous, or incomplete findings.

The standalone locator does not add its invoking interpreter as a candidate;
discovery explicitly supplies its own interpreter because Harbor validates in-process.
Each Harbor probe has a 10-second timeout and each NeMo Gym probe 60 seconds.
Each provider has a separate 390-second cumulative probe budget; uv enumeration
has a 10-second budget. This retains bounded execution without one provider
consuming another's probe allowance. These limits are not cold-start performance
measurements.

Selection precedence is explicit path, project-declared path, project environment,
then global environment. A broken explicit/project-declared path blocks fallback;
equal-priority compatible environments require a choice. Repeated sightings of
the same environment do not create ambiguity. Preserve lexical virtualenv Python
paths: resolving the executable symlink can select the base interpreter instead.

A project can declare exact locations and an additional version constraint:

```toml
[tool.eval-author.runtimes.harbor]
python = ".venv/bin/python"
requirement = ">=0.20.0,<0.24.0"

[tool.eval-author.runtimes.gym]
python = "/absolute/gym/.venv/bin/python"
cli = "/absolute/gym/.venv/bin/gym"
requirement = "==0.6.0"
```

Paths are resolved relative to `--repo`. Direct `project.dependencies` requirements
for these providers are also enforced. The standard-library parser accepts only
stable numeric releases and comma-separated `>=`, `>`, `<=`, `<`, `==`, `!=`
comparisons. Prereleases, markers, extras, URLs, and other constraint syntax are
unresolved, not silently ignored. Optional groups, lockfiles, shell activation,
and prose setup instructions are not automatically interpreted: read relevant
project guidance and supply exact paths with `--harbor-python` / `--harbor-cli`
or `--gym-python` / `--gym-cli`. Do not edit customer configuration to resolve
this. For unsupported requirements, report the limitation for explicit review.

Preserve `selection.<provider>.selected.python` and `.cli` across shell sessions.
Use that exact Python for Harbor discovery and pass the selected NeMo Gym CLI with
`discover.py --gym-cli /absolute/gym`. Discovery rechecks runtime capabilities;
its Harbor validators remain in-process, so launching it with an unrelated
Python cannot borrow another installation's readiness evidence.

When unresolved, return the candidates and actual errors to the calling flow.
Shared prerequisites complete the other accessible checks before asking only
for the missing location or choice. If no usable installation exists, follow
[Harbor setup](harbor-setup.md), or the selected project's NeMo Gym setup instructions,
subject to existing setup authorization. Do not reinstall over a broken runtime.

The policy's `test_profiles` identify pinned test configurations and their test
owners. A matching installed version does not prove those tests passed locally.
Supported means it meets the declared compatibility range; verified prerequisites
also require the listed capabilities. Private provider behavior, task backends,
credentials, and execution are still checked by later provider-owned validation.

Return selected command, interpreter, installed version, policy version, and
unresolved setup needs to the calling flow. Keep runtime evidence separate from
suite readiness. Inventory-only requests retain their existing no-probe boundary.

## Check for optional Harbor skills

Alongside the runtime check, look for
[Harbor's own skills](https://github.com/harbor-framework/harbor/tree/main/skills).
These are guides for the coding assistant working on evals; they are separate
from the Harbor runtime and from Eval Author. They are recommended, not required.
Check even when Harbor is missing or no Harbor evals were found.

Start with the current assistant's available-skill catalog. Also inspect existing
repository skill directories, such as `.agents/skills/` or `.claude/skills/`, and
skill locations exposed by the host or explicitly supplied by the user. Keep the
search bounded to those locations; do not scan the user's entire home directory.
Use skill metadata and source references to identify Harbor's skills, not a name
match alone. Names such as `create-task`, `create-adapter`, and `harbor-exec` are
examples from the linked collection, not an exhaustive installation checklist.
Do not mistake Eval Author itself or a Harbor source checkout for skills loaded
by the current assistant.

Record which skills are available in the session, which files were found but
whose availability is unconfirmed, and which locations were checked. If none
were found, say “not found in the locations checked.” If access or the host's
catalog is unavailable, report that limitation rather than asserting they are
not installed. A subset is useful; do not require every skill in the collection.

When presenting the runtime findings, briefly explain their purpose and
observed availability. When missing or unconfirmed, recommend them and link the
official collection. In shared onboarding, use the
[Recommendations section](shared-prerequisites.md#present-results-and-the-next-step),
including its heading and introduction. For example, when the search found none:

```markdown
### Recommendations

These suggestions are optional and are not required to continue.

- [Harbor's skills](https://github.com/harbor-framework/harbor/tree/main/skills)
  guide your coding assistant through creating and working with evals; none were
  found in the locations checked
```

When skills are available, name the relevant ones briefly instead of recommending
another installation. Include this advisory with the runtime findings. Do not
install or invoke skills as a detection step, ask the user to install them before
continuing, or add a new approval gate.
Their absence changes no readiness checks, `proven`, `runnable`, or exit code.
The script cannot determine host skill availability. Return these observations
with prerequisite-only findings; include them separately from evidence JSON in
the later full discovery report as described in [Step 6](../../eval-author-discover/SKILL.md#step-6-save-the-report).

Unsupported dependency forms (including extras, markers, compatible-release
operators, and direct references) are retained as provider-specific requirement
errors. Names are normalized across hyphens, underscores, and dots. The affected
provider remains unresolved; candidate evidence and the other provider's
selection are preserved.

Python probes use isolated mode, excluding user site-packages and PYTHONPATH.
Use a self-contained virtualenv. A package visible only in the invoking process
may therefore fail the isolated import check; that discrepancy is reported when
it can be detected.
