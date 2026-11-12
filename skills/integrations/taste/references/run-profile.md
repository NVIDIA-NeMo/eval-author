<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Generation run profile

Record the selected benchmark and run settings before generation. Reuse
confirmed decisions and authorizations; do not infer new compute approval
from a request to prepare or review a plan.

## Settings to resolve

| Item | Record for the selected run |
| --- | --- |
| Benchmark and count | Agent/suite identity and `k` distinct new examples |
| Prepared inputs and execution | Bundle identity, source/schema/prompt hashes, native runtime identity and current [proof receipts](backend-adapters.md); service identity only for the scripted runner |
| Authority | Confirmed ETHOS, approved audit specification, selected baseline and measurement report |
| Audit snapshot | Fixed source scope and comparison objective; later run results do not silently retarget generation |
| Selection | User-selected gap-agnostic or gap-targeted mode, fixed audit snapshot, selection and objective ledger; preserve upstream selection for comparison |
| Seeds | Explicitly selected source split, task identities and exclusions; no implicit held-out or generated-cohort reuse |
| Lengths | Selected distribution and treatment of empty references; use upstream support or a narrowly declared adapter |
| Sequence pipeline | Upstream adaptive training, validity feedback, weighted distance, clustering and recovery |
| Stage 3 executor | Codex following the skill protocol (default), or the optional scripted runner with a compatible EA service |
| Generation and repair | Sequence-conditioned candidate then fixture, bounded patches then regeneration, and one post-batch recovery pass under the [protocol](stage3-protocol.md) |
| Models | Configured roles for plausibility, generation, coherence, qualification and any optional diagnostics |
| Hinted contract | Whether diagnostics are selected; when used, their redaction, shuffle and attempt rules |
| Limits | Training draws, pool size, cluster count, repair/replacement ceilings and overall budget |
| Primary evaluation | Proven agent/model/simulator settings, response budget, repeats, seeds, reset and scoring |
| Outputs | Native task paths, private working directory, evidence directories and publication destination |
| Reporting | Separate native/supplemental reward, observed audit coverage, reference diversity, losses and costs |

Reuse compatible prepared resources and EA/native execution tools. Reuse an
existing task service when selecting scripted execution; do not require a new
adapter for skill-directed work. Record missing native validation/export support before scaling;
unavailable native execution need not block authorized sequence-only work.
Do not present a written profile as runtime evidence.

## Readiness and reuse

Use the shared [prepared-input and execution requirements](backend-adapters.md); this profile records
the selected implementation and evidence rather than defining another contract.

| Boundary | Evidence to inspect | Required result |
| --- | --- | --- |
| Tools, state and runner | Native schemas, actual initialized fixture, reset and scoring | Live/reference state parity, preserved identities and isolation |
| Upstream sequence pipeline | Selected imports, settings and checkpoints | Upstream training/selection executes with the selected seeds |
| Prepared inputs | Normalized seeds, policy and tools; task/fixture definitions for Stage 3 | Sequence stages run without native runtime imports |
| Task generation and repair | Native definitions/examples, shared prompts, selected executor and EA/native evidence | TASTE protocol governs generation/repair; EA/native tools validate and package |
| Optional hinted simulation | Actual simulator and communication contract | Hints are private; execution observes real tool results and does not invent user replies |
| EA qualification | Environment, construction and control receipts | Current-revision native checks pass; prior cohorts' receipts are not inherited |
| Export and storage | Task/fixture parity and stable-path inventory | Generated tasks use the same EA directory and lifecycle as other drafts |

## Output layout

Use EA's existing locations rather than a separate TASTE task tree:

```text
.eval-author/
  tasks/<task-slug>/                 # Delivered native task; stable across statuses
  .working/taste/<run-id>/           # Private generation work and upstream staging
  jobs/<run-id>/                     # Native execution output, when selected
  task-measurements/<task-slug>/     # Control and acceptance evidence, when used by the flow
  report.html                       # Existing suite review report
  README.md                         # Existing suite review and rerun guide
```

The scripted runner writes intermediate task sets in its run workspace;
skill-directed work retains private attempt payloads and EA/native receipts.
Keep those as generation evidence, then package with parity proof into stable EA
task paths. Reference the TASTE run, representative and source task in provenance;
do not move or duplicate accepted tasks into a second output tree. Register
new tasks as Draft through EA journey state. Acceptance follows the selected
EA flow's evidence rules.

## Checks to retain

- Live execution and reference/verifier replay use identical starting state,
  including record multiplicities where native identities repeat
- State-only rewards may miss incorrect answers; use separately scoped outcome
  checks where intended behavior requires them
- A returned artifact path does not prove that the requested artifact exists
- Safe failures need observable triggers and truthful responses; missing
  witnesses do not prove global dataset absence
- Hinted traces stay separate from unhinted primary results
- Preserve rejected candidates, helper errors, repairs and stop checkpoints
- Preserve valid hard tasks and primary failures; larger-budget diagnostics
  cannot replace primary results
- Distinguish reference diversity from observed coverage and disclose semantic
  measurement scope

Resolve missing method or budget settings before dependent execution. Once the
run is authorized, continue within that scope without repeated readiness
questions or mandatory manual approval of every generated task.
