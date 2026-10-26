<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Harbor construction, proof, and publication

Read this entire procedure before constructing or proving a Harbor candidate.

For Harbor output, under `<task-dir>/task/`, create the smallest
Harbor task that reproduces the initial state and objectively verifies the
generalized outcome. Read and follow `task-contract.md` for the
required layout, the separate no-network verifier contract, and the
reviewer-facing README sections, and `environment-integrity.md` for
agent-network, contamination, portability, repeat-run, and negative-control
requirements. Never copy private trace payloads into the task; include only the
minimal files needed to reproduce the starting state. Human-supplied or
reviewed `Relevant experience` is necessary for readiness; never invent it.

Before recording proof inputs, author the private construction contract in
[references/construction-qualification.md](construction-qualification.md).
New Harbor workspaces require requirement-to-check mappings, starting-artifact
provenance, executed setup/reach probes, a reviewed valid alternative, and a
bypass control for each declared dependency. `validate-task` checks declarations;
only retained execution can establish qualification. Keep measured difficulty
separate from these checks.

For an authorized cohort of fresh solutions, use the optional
[solution-review diagnostic](solution-review.md) to compare reviewed
correctness with verifier scores. Freeze the full cohort before execution and
retain verifier-owned submission snapshots. This diagnostic does not change
readiness; investigate findings before handoff.

Start `task/README.md` sections with `## Input`, documenting the generalized
request and linking its instruction and fixtures without exposing private trace
payloads. Follow with separate, nonempty `## Behavior under test`, `## Expected result`,
and `## Grading` sections, preserving all native task-contract sections. In Grading,
link the actual test entry point, verifier implementation, and criteria or judge
files; verify the relative Markdown links. Refresh the private
`.eval-author/report.html` and `.eval-author/README.md` at creation and validation
handoffs using the [Report generation](../../eval-author/references/suite-readme.md#report-generation).
Do not change discovery or readiness to generate this reporting view.

Create `<task-dir>/README.md` for operational/export instructions using
[Review and rerun guidance](../../eval-author/references/suite-readme.md#review-and-rerun-guidance), separate
from the reviewer-facing `task/README.md`. Map the generalized outcomes to the
task and its scored checks, documenting when selecting one item runs the whole
task. Label NOP/Oracle commands as task controls; if no real-agent configuration
exists, state that agent evaluation is pending instead of inventing one.
Present and link the case inventory once authored, identifying the reviewed
source trace as provenance. Refresh it after validation with links to the
generated task's control results and available execution traces.

`tests/test.sh` must emit one `check-id\tPASS|FAIL` row per scored check to
`/logs/verifier/results`; read and follow `check-grammar.md`, including its outcome-by-outcome
review and executed copy probes when transcription could bypass requested behavior.

Work in an authoring loop, not one pass. `validate-task` lints the tree with
remediation hints; fix every issue it reports. Then `probe` runs one
diagnostic NOP and Oracle pair in the real container; design checks so probe
NOP fails every scored check and probe Oracle passes all of them. Probes are
diagnostic-only, capped per task revision, and never accepted as proof — see
`environment-integrity.md`.

Execute the repeat-run protocol in `environment-integrity.md`.
For example, record the first NOP job's inputs and then run it:

```bash
python <skill_dir>/scripts/trace_environment.py record-run-inputs \
  --task-dir <task-dir> --arm nop --job-dir private/jobs/nop-1
harbor run -p <task-dir>/task -a nop \
  --jobs-dir <task-dir>/private/jobs --job-name nop-1
```

Repeat with `nop-2`, `oracle-1`, and `oracle-2`, selecting the matching arm
and agent. Follow the integrity reference to declare and run the negative
control. Also run the declared alternative and dependency bypass controls using
the construction reference. Retain every exact Harbor job and use the reference's
`record-validation` command; proof requires the per-check rows from
`check-grammar.md` in every job. Never hand-write rewards, job IDs,
exceptions, or checksums.

Failed proof remains technical evidence, and a fixable defect starts the
bounded repair loop: `record-repair` with a reason code, then a fresh manifest
and proof set (at most three repairs). Do not weaken the verifier to make
Oracle pass.

If Harbor or Docker is missing, technical status is `not_run` and the environment
is `unproven`; do not describe it as ready. The integrity reference defines the
conditions for technical status `passed` and the limits of that claim; passing
proof does not establish human review.


## Harbor batch and publication

For a multi-task run, write a checked-in manifest with stable task IDs and
source paths. This makes the denominator explicit and reruns idempotent:

```json
{
  "schema": "nemo.eval_author.trace_environment_batch.v1",
  "members": [
    {"task_id": "stable-id", "atif": "private-source.atif.json", "source_kind": "atif"}
  ]
}
```

Keep a real manifest's source paths private if they reveal internal layout.
For a generated batch, refresh the shared HTML catalog; retain a collection-root
`README.md` only for the functional execution/export handoff with the
explicit runnable member set, full-collection and selected-member commands,
and links to member guides. Distinguish blocked and no-candidate members;
do not point Harbor at the mixed workspace parent as if it were a task dataset.
`batch-prepare` prepares missing members and resumes existing ones;
`batch-status` reports every member. `denominator` must equal the selected
source set — never report only candidates or successes. A malformed workspace
stays in the report as `invalid` without hiding other rows.

Before any export, including `no_candidate`, read and follow
`publication-review.md`. Prepare the complete private publication
preview, review every exported file and path, and attest its exact digest before
exporting. Trace privacy review and `--human-reviewed` do not replace this gate.
Any change to the public product requires a new publication review; a successful
`check` alone is not publication approval.

Report the task ID, `candidate` or `no_candidate`, state basis, ground-truth
availability and artifact count, required software and licensing constraints,
environment status, technical status, check evidence mode, repair count,
review status, verifier isolation, privacy review status, NOP and Oracle
rewards when run, construction qualification (historical proof is `unmeasured`),
and paths to `summary.md` and the generated task. A
`valid: true` check proves the recorded files are internally consistent;
Harbor is the authority for whether the task actually runs.

