<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Gap-targeted TASTE selection

Use this procedure only after the user selects gap-targeted mode. Keep the
approved audit and baseline fixed for the run. Unmeasured or unclear items are
not established gaps; distinguish them from measured uncovered items.

Before selection, record each objective's audit ID, trigger, expected behavior,
evidence requirements and relevant policy and tools. Preserve the upstream
Stage 2 pool, clusters, medoids, plausibility decisions and original selection.
Do not add hand-written actions or use target-agent outcomes to select cases.

Inspect plausible learned representatives and, where retained by Stage 2,
their validated cluster members. Select diverse sequences that can reasonably
support the agreed objectives, preferring distinct clusters and gap objectives.
For each choice record its exact ordered actions, cluster and pool lineage,
plausibility evidence, objective, rationale, expected trigger and response,
and whether the trigger depends on fixture or customer behavior. Record
considered candidates and reasons for rejection. A tool name alone does not
prove a policy case can be constructed. Do not claim an unmatched objective
is covered; record unmatched objectives and any task-count shortfall.

Carry the objective, requirements and rationale into Stage 3 candidate and
fixture generation and all repairs. Verify the trigger in the initialized
fixture and request, and ensure native and independent checks distinguish the
expected response from plausible failures. Preserve the exact learned ordered
sequence, including repetitions; if it cannot support the objective, record the
failure rather than changing the sequence. Apply the ordinary bounded retries,
retaining all evidence. Recovery replacements require the same objective and
lineage checks; do not silently substitute an unrelated objective or sequence.

The skill-directed executor supports this mode. The optional scripted runner
does not implement this selection and objective handoff; do not claim a
gap-targeted scripted run unless that support is added and verified. Keep
hinted feasibility separate from qualification and unhinted agent measurements.
Report intended objectives, validated tasks, actual coverage witnesses, source
and mode, shortfalls, and the limits of comparing against gap-agnostic TASTE.
