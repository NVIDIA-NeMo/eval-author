<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Eval Author determinism plan

Eval Author should make the same operational decisions from the same request,
repository evidence, policy, and runtime capabilities. Move mechanical decisions
into validated scripts, retain the evidence behind each decision, and measure the
variation that remains in model judgments and live execution.

This plan covers the whole workflow: routing, prerequisites, discovery, audit,
task authoring, execution evidence, and reporting. The runtime work on
`design/deterministic-discovery` is one part of it. The implementation inventory
below describes that branch through `a8a3c37`; the roadmap describes further work,
not completed acceptance. It does not establish a live CI or provider-run result.

## What must remain stable

There are three distinct contracts:

| Contract | Required behavior | How to check it |
| --- | --- | --- |
| Mechanical decisions | Identical inputs produce identical selections, check statuses, coverage arithmetic, and acceptance decisions. | Repeat script runs against frozen inputs and compare structured results. |
| Workflow behavior | A request selects the intended flow, respects its authorized scope, and stops at the correct handoff. | Observe agent tool calls, selected skill, and created or changed artifacts. |
| Evidence and explanation | Reports preserve the measured facts, uncertainties, and next steps. | Render structured results directly; evaluate model-written explanations against those results. |

Exact prose, model trajectories, service latency, and live task rewards are not
promised to be identical. A fixed prompt or seed alone cannot establish that
promise. Live execution needs repeated trials with retained failures and explicit
runtime, model, task, and input provenance.

For example, a file-only request to find evaluations must not trigger runtime
installation, an audit, or task creation. Two compatible environments at equal
priority must produce an ambiguity that needs a choice. A second run cannot turn
missing evidence into a pass just because its explanation sounds more confident.

## Shared implementation boundary

The core `eval-author` skill owns helpers and references used across flows:

- [Runtime locator](../skills/eval-author/scripts/locate_runtime.py),
  [policy reader](../skills/eval-author/scripts/runtime_policy.py), and
  [runtime policy](../skills/eval-author/scripts/runtime-policy.json).
- [Runtime prerequisites](../skills/eval-author/references/runtime-prerequisites.md)
  and [Harbor setup](../skills/eval-author/references/harbor-setup.md).
- [Milestone handoffs](../skills/eval-author/references/milestone-checkins.md).

Provider-specific discovery stays in `eval-author-discover`; audit measurements
stay in `eval-author-audit`; task execution receipts stay with their authoring
flow. Share a helper when the decision is shared. Avoid moving provider-specific
logic merely to make the directory tree flatter.

Runtime resources must ship inside skill directories. Repository `tools/` owns
developer and CI tooling, which skill-only installations do not include. Relative
imports and documentation links must work in a copied sibling-skill installation,
from an unrelated working directory, without the repository on `PYTHONPATH`.
Static type-checker search paths must match those runtime import paths.

## Existing implementation and its limits

| Area | Existing mechanism and checks | Remaining boundary |
| --- | --- | --- |
| Runtime selection | Shared policy, bounded locator, explicit selection precedence, interpreter/CLI pairing, and flow-specific capability probes. [Locator tests](../tests/test_runtime_locator.py) cover ambiguity, broken explicit paths, aliases, malformed declarations, limits, and copied installation. | Supported version ranges are broader than the pinned test profiles. Matching a profile does not prove it was exercised locally. |
| Discovery | [Discovery script](../skills/eval-author-discover/scripts/discover.py) sequences Probe, Explore, Judge, and Solve; provider code owns validation. [Contract tests](../tests/test_skill_contract.py) exercise missing runtimes, dropped tasks, failed configuration, sampling, and report behavior. | Full discovery can execute provider commands and tasks. It is not the file-only inventory path. |
| Inventory and sampling | Harbor exploration sorts inputs and fingerprints selected files. Solve selects evenly spaced entries, including first and last when the sample permits. Its current budget is four datasets/manifests in total and four tasks per Harbor dataset. | The fingerprint is not a complete runtime or repository snapshot. Stable sampling needs stable ordered inputs and does not prove the whole suite executes. |
| Audit | [Audit scripts](../skills/eval-author-audit/scripts/audit_spec/README.md) validate the denominator, measure traces, and aggregate coverage. Contract tests reject stale source digests, stale judgments, mismatched denominators, and unknown items. | Proposed audit content and semantic judgments still require review. An unmeasured capability is not automatically a task-generation target. |
| Task authoring | [Pipeline tests](../tests/test_task_pipeline.py) cover stable slugs, duplicate-run rejection, actionable-gap selection, and required execution receipts. [Evidence tests](../tests/test_task_evidence.py) exercise receipt checks using synthetic runtimes. | A synthetic receipt test establishes checking behavior, not real-agent success or task quality. |
| Trace-derived environments | [Trace environment tests](../tests/test_trace_environment.py) cover changed-task evidence, stale jobs, publication review invalidation, and source mutations. | Replay fidelity and acceptance remain limited to the tested task and source evidence. |
| Reporting | Discovery has a [structured report renderer](../skills/eval-author-discover/scripts/render_report.py). The [semantic regression pilot](semantic-regression.md) measures finding retention and consistency over frozen evidence. | Semantic consistency does not establish factual correctness, full workflow execution, or evaluation quality. |
| CI | [CI](../.github/workflows/ci.yml) declares policy consistency, lint, formatting, type checking, Python 3.12/3.13 tests, and separate Gym compatibility routes. [Skill evaluation](skill-evaluation-ci.md) adds advisory checks. | A declared job, a coverage plan, or a skipped check is not a successful run. Each claim needs evidence for its actual revision and environment. |

## Roadmap and completion checks

The phases are ordered by dependency. New gates should begin with explicit test
cases and expected evidence; a phase is complete only when its checks have run
and the results are retained for the relevant revision.

### Phase 1 Centralize prerequisites

The branch implements the shared runtime policy, locator, and setup references.
Keep supported ranges, installation pins, test profiles, and capability checks in
that policy rather than duplicating them in prompts or setup commands.

The selection contract is explicit paths, project declarations, project virtual
environments, then global candidates. Broken authoritative paths block fallback.
Distinct compatible environments at equal priority remain ambiguous. Incomplete
searches, timeouts, and unsupported requirement syntax remain unresolved. Preserve
virtualenv identity rather than resolving a Python symlink to its base binary.

Completion checks:

- Run the locator tests and policy consistency check.
- Verify both providers for each relevant flow and pin; keep supported-range
  claims separate from the versions actually tested.
- Exercise copied skill installations and isolated interpreter invocations.
- Run lint, formatting, and type checks after every helper relocation.
- Preserve candidate failures and selected Python/CLI paths in prerequisite
  evidence. Do not install or activate an environment merely to locate one.

### Phase 2 Make routing and scope observable

Define a table of requests, selected flows, prerequisites, allowed writes, and
terminal handoffs. Cover generic eval help, explicit discovery, readiness,
explicit audit, first-eval planning, recommendations only, task creation, and
trace inspection. Include follow-up requests that broaden or narrow scope.

Keep user intent interpretation in the assistant, but make the selected operation
and its allowed effects inspectable. Where a script can enforce a boundary,
enforce it there. Prose instructions alone do not prove compliance.

Completion checks:

- Replay exact prompts and bounded paraphrases in disposable repository fixtures.
- Assert the selected flow and actual tool calls, not only the final answer.
- Compare before/after file manifests. Inventory must preserve source files;
  recommendations-only requests must not create executable tasks.
- Preserve explicit audit routing. Missing runtimes must not silently redirect
  the user into an unrelated workflow.
- Exercise ambiguous requests and explicit continuation across handoffs. Record
  the reason for any required question without introducing redundant approvals.

### Phase 3 Make discovery comparison repeatable

Create a frozen discovery fixture matrix with Harbor, Gym, mixed suites, no
evaluations, malformed files, ignored paths, unreadable inputs, missing variables,
missing backends, and tasks rejected or dropped by native validators.

Define a canonical decision projection of the report: inventory membership,
provider assignment, check identity/status/severity, selected sample, and reasons
for skipped or unproven work. Preserve the full raw report beside it. This
projection and a general repeat-run comparator are proposed work.

Completion checks:

- Repeat discovery against the same fixture and controlled runtime; compare the
  decision projection and exit status.
- Vary file creation order, process working directory, and concurrent completion
  order without changing the logical inputs. The selected sample and decisions
  must remain stable.
- Change one config, task, policy, or runtime capability at a time and confirm the
  affected evidence changes. Document the fingerprint's included and excluded
  inputs; never use it as a complete environment identity.
- Preserve missing, blocked, skipped, and unsampled states. Successful execution
  of a sample must not become a claim that every task passed.
- Test provider failures independently so a working provider cannot hide an
  unchecked portion of a mixed suite.

### Phase 4 Bind audit and authoring decisions to evidence

Extend existing digest and receipt checks into a documented contract from source
evidence to denominator, measurements, proposal, generated task, and acceptance.
Each downstream artifact must identify the upstream inputs that justified it.

Completion checks:

- Repeat measurement and aggregation on frozen ATIF inputs and compare item
  classifications, counts, denominator identity, and actionable gaps.
- Keep semantic judgments bound to the exact item and trace. Changing either must
  invalidate the judgment rather than reuse it silently.
- Reject duplicate run identities, copied measurements presented as fresh trials,
  stale task results, missing controls, and acceptance without required receipts.
- Run positive and negative verifier controls. A task that rewards an empty or
  incorrect response must not be accepted because its reference solution passes.
- Require the flow's prescribed real-agent repetitions and measured coverage
  before accepting a generated task. Preserve all attempts, including failures.
- Mutate reviewed or accepted content and verify that the affected review or
  acceptance becomes stale. Scope approval to the actual artifact contents.

### Phase 5 Make reporting preserve the result

Use structured outputs for selections, arithmetic, statuses, evidence links, and
next-step eligibility. Let the assistant explain those fields and unresolved
choices without recomputing them from prose.

Completion checks:

- Render a frozen structured result repeatedly and compare its factual fields.
- Seed contradictions, omitted failures, and unsupported success claims into
  candidate summaries; the report check must detect them.
- Retain distinctions between inventory, prerequisite readiness, validation,
  sampled execution, measured coverage, and final task acceptance.
- Run independent semantic batches against a reviewed fixed baseline. Preserve
  failed and empty reports; do not choose the best batch or loosen thresholds
  after observing a regression.
- Keep report-stability findings separate from task quality and source truth.

### Phase 6 Establish an acceptance matrix across hosts

Run the same reviewed workflow cases through each supported assistant or harness
and installation form. Record its version and the exact loaded skill contents.
The routing fixture matrix comes before this broader comparison so different
hosts are tested against the same expectations.

Completion checks:

- Cover checkout-based and copied skill installations, then each supported host.
- Separate deterministic script failures, workflow boundary violations, provider
  execution failures, and model-output variation in the results.
- Keep script invariants as required checks. Promote a live or semantic check to
  a required gate only after its repeatability, failure sensitivity, cost, and
  incomplete-result behavior have been reviewed.
- Report pass counts with attempted, skipped, and incomplete counts and their
  denominators. Publish no cross-host equivalence claim from one successful run.

## Repeat-run evidence contract

The proposed comparison record should contain the following fields. Existing
artifacts carry subsets; this is a target for consistent evidence, not a claim
that a unified schema already exists.

| Evidence | Required contents |
| --- | --- |
| Source identity | Repository revision, dirty-state manifest or digest, loaded skill revision/content digest, fixture and input digests. |
| Request | Exact prompt, selected flow, relevant prior authorization, and requested scope. |
| Runtime | Policy/schema versions, provider version, Python/CLI pairing, platform, backend or image identity where relevant. |
| Model execution | Host/harness and model identifiers, available generation settings, attempt identity, and tool-call trace. |
| Decision | Selected candidates, ordered inventory, check results, selected sample, explicit unresolved reasons, and exit status. |
| Artifacts | Input/output locations and digests, measurements, execution receipts, report, and review or acceptance status. |
| Accounting | Planned and attempted cases, failures, skipped and incomplete checks, limits reached, and retained raw evidence. |

Compare repeated runs in two ways. With inputs and environment frozen, assert
identical mechanical decisions. With one input deliberately changed, assert the
specific invalidation or outcome change expected from that mutation. Repeating
an unchanged happy path alone cannot show that a checker catches regressions.

Allow normalization only for reviewed incidental fields, such as timestamps,
elapsed time, and mapped temporary roots. Retain the originals. Do not discard
errors, runtime identities, status changes, missing evidence, or task mutations
to obtain a match. If an external dependency changed, record a different input
condition rather than labeling the difference nondeterministic.

## Commands and evidence collection

Run these from the Eval Author checkout after its documented development setup:

```bash
uv run --locked python tools/check_runtime_policy.py
uv run --locked pytest tests/test_runtime_locator.py tests/test_skill_contract.py -q -rs
uv run --locked pytest tests/test_task_pipeline.py tests/test_task_evidence.py tests/test_trace_environment.py -q -rs
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked ty check --python-version 3.12
uv run --locked ty check --python-version 3.13
```

CI runs type checks on Linux. On macOS, add `--python-platform linux` to reproduce
that target; native macOS checking can flag Linux-only extended-attribute APIs in
tests. This changes the static target, not the host on which tests execute.
Use the full [development checks](../DEVELOPMENT.md#development) and actual CI
matrix for release evidence, including policy, licensing, and copyright checks.

The [manual discovery scenarios](../tools/discover_scenarios.py) exercise runtime
and backend transitions. Listing them with `--list` is inspection; running them
can install runtimes, change their work directory, and execute provider workloads.
Follow their prerequisites and the user's authorized execution scope. They are
not a substitute for an automated comparison harness.

Use [SkillEvaluator CI reports](skill-evaluation-ci.md) for workflow evaluation
and the [semantic pilot](semantic-regression.md) for repeated report comparison.
Both retain their documented live-execution boundaries. Writing this plan does
not enable scheduled inference or run customer evaluations.

## Completion and maintenance

The broader plan is complete when each phase has reviewed cases, executable
checks, and retained results for its claimed runtime and host matrix. Track
remaining cells as unmeasured or incomplete until they have evidence. A failing
invariant remains a failure even when an overall score improves.

For each implementation change, record the invariant, test cases, relevant
revision, executed matrix, results, and remaining gaps. Policy, provider, model,
fixture, or measurement changes require a new comparison under identified inputs.
Keep previous results and reviewed baselines so regressions remain explainable.
