<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Construction qualification

**Experimental.** New Harbor workspaces require this contract before proof.
It codifies verifier qualification, declared starting-file provenance and tested
dependencies. It does not measure difficulty, establish all possible valid
solutions, or reconstruct missing historical truth. Gym uses native evidence;
the helper's qualification receipts apply only to Harbor.

## Author the private contract

Write `private/construction.json` with these exact top-level fields:

| Field | Content |
| --- | --- |
| `schema` | `nemo.eval_author.construction.v1` |
| `candidate_sha256` | SHA-256 of the exact `candidate.json` bytes |
| `trace_sha256` | SHA-256 of the exact `safe/trace.atif.json` bytes |
| `requirements` | Stable IDs joined to candidate requirement indices and scored check IDs |
| `probes` | Executable setup and behavior-reach observations described below |
| `controls` | Declared negative, alternative, optional copy, and dependency bypass controls |
| `dependencies` | Directed requirement edges, each with its own bypass control; `[]` if none |
| `starting_state` | Declared input inventory and provenance described below |
| `review` | `{"reviewer_kind":"agent","note":"<concrete review findings>"}`; human review is also supported |

Use stable kebab-case IDs. Each candidate requirement index must appear exactly
once. A check may cover more than one requirement, but the union of declared
checks must exactly match every proof run's scored check IDs. For example:

```json
[
  {"id":"repair","candidate_index":0,"checks":["repair-and-preserve"]},
  {"id":"preserve","candidate_index":1,"checks":["repair-and-preserve"]}
]
```

The digest prevents indices silently changing meaning. Review the mapping
against the visible instruction and actual assertions: schema validation cannot
make an unstated requirement public. Keep preservation and prerequisite checks
inside their scored outcome so an untouched environment fails every scored check.

`validate-task` checks the construction contract during authoring. Re-run it
after changes. Run `record-reproducibility` after the task and verifier files are
final. Candidate, safe trace, manifest, task, control source or probe changes
invalidate existing proof inputs; retain the old evidence and run fresh jobs.

## Record starting-file provenance

`starting_state` has exactly `cutoff_step_id`, `roots`, `artifacts`,
`external_inputs`, `no_files_reason` and `review`.

- `cutoff_step_id` identifies the chosen starting point in the trace's list
  order, not numeric step-ID order. Explain why that point represents the task
  start in `review.note`.
- `roots` lists directories under `task/environment/` containing starting inputs.
  Every file beneath these roots must have exactly one artifact entry. Symlinks
  and nonregular files are rejected. Do not include reference solutions or graders.
- Each artifact has exactly `path`, `sha256`, `basis`, `source`, `edits`, `review`.
  `path` is workspace-relative; `sha256` identifies the actual file bytes.
- `external_inputs` lists objects with exactly `description`, `identity`,
  `review` for relevant base-image, downloaded or runtime-created state not in
  the input roots. Identity is recorded text requiring review, not automatic
  verification of immutable content. Use existing runtime/portability evidence
  to validate it; unresolved external state must remain an explicit limitation.
- Set `no_files_reason` to `null` for a nonempty file inventory. If no starting
  files are needed, use empty roots/artifacts and explain the scope explicitly.

The supported bases are:

| Basis | Required evidence |
| --- | --- |
| `observed` | `source` is `{"step_id":N,"pointer":"/observation/results/0/content"}` addressing exact text within that step at/before cutoff; UTF-8 bytes must equal the starting file; `edits: []`. |
| `reversed_edit` | `source` addresses a recorded later full-text snapshot. `edits` is a nonempty list of `{before: <source-reference>, after: <source-reference>}`. Each pair addresses text in the same later mutation step. Apply pairs in strictly descending trace order; each `after` must match exactly once and is replaced by `before`. The resulting bytes must equal the starting file. |
| `authored` | `source: null`, `edits: []`, concrete review, and candidate `state_basis: "reconstructed"`. Preserve this classification even if later execution validates the fixture. |

JSON pointers are relative to the selected step. Exact reversal validates only
the declared edits: review that all intervening relevant changes are accounted
for and the cited fields actually represent file content and edits. Ambiguous,
binary or unsupported transformations cannot claim this exact-reversal proof;
retain a reconstruction with review or record the limitation. Deliberately broken
starters are valid inputs; provenance does not require them to parse or run.

The reported scope is **declared input files**, not the entire image or runtime.
The helper cannot discover all files introduced by arbitrary build or startup
code. Review that inventory boundary explicitly; do not hide relevant state by
choosing an empty root.

## Observe setup and behavior execution

Copy `scripts/qualification_runtime.py` unchanged to
`task/tests/qualification_runtime.py`. Write the manifest's exact `probes` array
to `task/tests/qualification-probes.json`. Both become part of the task digest.
Each probe has these exact fields:

```json
{
  "id":"entry-point-reached",
  "kind":"reach",
  "argv":["python","/tests/probe_interface.py"],
  "cwd":"/workspace",
  "timeout_seconds":10,
  "json_pointer":"/processed_records",
  "equals":2,
  "assertion_path":"task/tests/probe_interface.py"
}
```

Include at least one `setup` probe, with all setup probes before reach probes.
Check prerequisites independently of the behavior under test: an intentionally broken application is not necessarily a
broken environment. A `reach` probe must exercise the supported interface and
observe a task-specific property establishing that it reached the relevant path,
even for the deliberately incomplete control. At least one declared reach
observation for each negative/copy/bypass control must be absent from **every
NOP run** and present after the control. Merely showing that an unchanged
interface is callable cannot qualify progress. Observe a relevant partial
result, artifact or trusted event; do not add an arbitrary marker solely to make
this test pass. Do not require the correct final answer merely to establish
reachability. NOP setup probes must also pass so broken setup cannot create a
false progress distinction.

Keep reach work distinct from full correctness checking. For native artifacts,
exercise the relevant interface with a bounded observation; do not repeat an
expensive full validation merely to establish reach. Exercise the probe on the
valid alternative and each defective control before accepting qualification.
If a subprocess times out or cannot start, preserve that error rather than
turning it into a false observation or weakening the requirement.

The probe wrapper must derive observations from the invoked component, not emit
constant success markers. Verify imports target the submitted checkout; verify
test collection is nonempty where relevant. Observe trusted side effects or
instrument the verifier-owned invocation where application output could be
spoofed. The helper confirms that `assertion_path` exists, not that argv executes
it or that an assertion is semantically adequate. Review those relationships.

From the separate, no-network verifier, before producing the ordinary check
rows and reward, run:

```python
import json
from pathlib import Path
from qualification_runtime import run_probes

probes = json.loads(Path("/tests/qualification-probes.json").read_text())
run_probes(probes, Path("/logs/verifier/qualification.json"))
```

Keep verifier logs fresh and verifier-owned; never copy a solver-supplied receipt.
The POSIX helper runs argv without a shell, limits each probe to 30 seconds and
256 KiB of combined output, records exit status and the selected JSON value,
and refuses to overwrite old evidence. A matching value counts only with exit
zero and no start, timeout, output-limit or parse failure. Missing evidence is a
contract error; an executed setup/reach failure is retained as failed qualification.
Ordinary scored checks and rewards remain authoritative for task outcomes.

Execution receipt v2 retains combined output in verifier-owned
`qualification-output/<probe-id>.bin` files, bounded to 256 KiB each and 32 probes.
The receipt binds each exact locator and retained-byte hash, explicitly labels
nonzero exits and output truncation, and is limited to 2 MiB. Oversized parsed
observations retain an explicit failed result within that bound. Validation rereads
the output and recomputes successful, invalid and oversized JSON observations;
changed output or observation status is rejected. An output-limit hash identifies the retained prefix,
not unseen trailing output. Keep raw output private: it may contain submitted
data or diagnostic details and is not included in CLI summaries or export.
Historical v1 receipts require their original skill revision; the current runtime
requires v2 receipts and retained output.

The verifier remains reviewed code, not cryptographically trusted code. Retained
local files can be fabricated by their owner, just like existing Harbor results.
Never hand-write execution receipts, rewards or observed values. Unit tests may
use synthetic provider receipts, which are not live Harbor evidence.

## Declare controls and dependencies

Each control has exactly `id`, `arm`, `requirements`, `fails`, `reaches`,
`dependency`, `rationale`, and `review`:

- `arm` is `negative`, `alternative`, `copy`, or `bypass`.
- `requirements` references mapped requirement IDs.
- `fails` lists mapped checks expected to fail, nonempty except for alternatives.
- `reaches` lists nonempty `reach` probe IDs; all must pass after setup succeeds.
- `dependency` is a dependency ID for a bypass; otherwise `null`.
- `rationale` explains the omitted behavior or why the alternative is a valid,
  materially different solution. Review its public-contract validity independently
  of its reward. Different source hashes alone do not establish independence.

Declare at least one negative and one alternative control. An alternative covers
every requirement, has `fails: []`, and must pass every scored check with reward
one. Negative/copy/bypass controls require reward zero and every declared failing
check to fail, after their setup/reach probes pass. Every declared control needs
at least one retained run, including optional copy controls once declared.

A dependency is exactly:

```json
{"id":"repair-needed","prerequisite":"repair","dependent":"preserve","bypass_control":"skip-repair"}
```

The requirement graph must be acyclic. Its bypass must identify this exact edge,
reach the relevant behavior, and fail a check mapped to the dependent requirement.
An unrelated failed check does not qualify the edge. Review that the control
actually omits that prerequisite while holding other relevant conditions fixed.
One observed bypass failure supports that tested dependency/control, not a claim
that every possible solution requires the author's preferred sequence.

## Run and retain proof

The existing two NOP and two Oracle jobs remain required. For every custom arm,
record inputs before the job exists, including its manifest control ID:

```bash
python <skill_dir>/scripts/trace_environment.py record-run-inputs \
  --task-dir <task-dir> --arm alternative --control-id equivalent \
  --job-dir private/jobs/alternative-1 \
  --control-agent equivalent:AlternativeSolution \
  --control-source private/equivalent.py \
  --control-rationale "<why this independently reviewed solution satisfies the contract>"
```

Run the custom agent through Harbor with the same task, isolation and job path
as the integrity reference. Repeat for each declared negative, copy or bypass
control using its matching arm and ID. Pass `--alternative-job-dir` and, for each
bypass, `--bypass-job-dir` to `record-validation`, alongside its existing proof
arguments. These flags repeat for multiple jobs. Aggregate-only proof cannot
qualify construction. No additional CLI or authoring agent is introduced.

## Status and compatibility

New `init` workspaces use summary v3, run-input v2 and validation v7. Summary v3
requires the manifest; deleting it fails validation. The creation marker also
rejects a summary-only downgrade to historical proof. These are edit-detection
tripwires, not tamper-proof storage. `finalize`, `check` and publication rederive
proof from retained results and execution receipts. Changed/missing evidence
fails validation; fixing a task uses the existing bounded repair protocol.

Qualification is `not_run`, `passed`, `failed`, or `not_applicable` for
no-candidate outcomes. The reported `difficulty` remains `unmeasured`. Runtime
proof, construction qualification and human review are distinct claims.
For new workspaces, `validation.passed` and the existing technical-status field
aggregate both runtime outcomes and construction qualification; consistent
rewards alone cannot make them pass. Individual rewards and setup/reach outcomes
remain available in the retained private run evidence for diagnosis.

Retained summary-v2/run-input-v1/validation-v6 workspaces remain readable and
report construction qualification as `unmeasured` in command/publication output.
Do not add a new construction manifest to a historical workspace in place;
initialize a fresh workspace and rerun proof. Historical summaries and receipts
remain unchanged. Publication includes aggregate counts/status, not the private
manifest, source pointers, control rationales or raw observations. Review the
entire existing publication preview as usual; task verifier files are included.

## Maintainer verification

The repository's default pytest collection includes these skill-local regressions.
For a focused maintainer check, run:

```bash
uv run --locked pytest -q skills/eval-author-trace-environment/evals/tests
```

These cover synthetic provider receipts, real local probe execution, provenance,
dependency controls and finalization/publication integrity. They do not replace
live provider qualification of a generated task.

### Evidence limitations and repair history

Review identities, including `reviewer_kind: human`, are unverified self-declarations,
not authenticated approvals. New workspaces carry a write-once creation marker;
changing only the summary schema cannot select the historical qualification path.
This is a tripwire, not protection against an author deleting or forging evidence.
Historical `unmeasured` qualification is not qualified.

Record a repair before editing the candidate or construction contract. Repairs
retain both files alongside prior proof reports; `check.contract_changes` lists
changes against those snapshots for review, without asserting that changes are valid.
Reverse edits currently require strictly decreasing trace steps: multiple edits to
the same file within one step must be represented by a single before/after reversal.
Receipts bind retained files consistently; they do not authenticate execution time
or prove that Harbor actually ran.

Probe observations reject nonfinite JSON and are capped at 48 KiB of serialized JSON each (`observation_limit`), keeping 32-probe receipts below the 2 MiB reader limit. Command output overflow remains `output_limit`.

The current producer and consumer require execution receipt v2 together. Historical
v1 receipts are not accepted with the current bundled runtime; inspect them with
the original skill revision or rerun the task with the current producer and fresh
bindings. Editing the schema cannot disable retained-output checks.

Both successful and invalid/oversized parsed observations are rederived from the
retained bytes. Output cannot authenticate exit codes, process start failures,
timeouts, or whether omitted bytes existed after truncation. These execution facts
remain trusted producer claims; in particular, stdout alone cannot disprove a
forged `nonzero_exit`. Hash binding establishes consistency, not trusted execution.
