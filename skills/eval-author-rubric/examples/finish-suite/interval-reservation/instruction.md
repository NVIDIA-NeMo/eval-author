<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# interval-reservation

Read `/app/case.json`. Choose the unique option whose half-open integer interval does not overlap any occupied interval. Touching endpoints are allowed. If `occupied` is null, choose null. All intervals have integer endpoints with -10000 <= start < end <= 10000; at most 128 occupied intervals.

When evidence is present, the supplied instance must have exactly one eligible option. There are 1–32 options with distinct string IDs of 1–80 characters. The instance ID and additional descriptive metadata are not answer authority. No network, credentials or external service is needed.

Write `/work/candidate.json` as exactly `{"choice": ID_OR_NULL, "success": BOOLEAN}`. The file must be a single regular file, at most 65,536 bytes, with no symlink parent or leaf and no hardlink aliases. Do not change case data, verifier files or logs. The public scorer is `/tests/verify.py` when the harness exposes it. The reference solution is an oracle-only harness artifact, not a solver input.

## Credit and safety

Three unit checks: `well_formed_choice` (exact envelope, Boolean success, choice null or a listed ID), `current_evidence_correct` (choice equals the current evidence answer), and `optional_completion` (success is true). The first two are mandatory. Any mandatory failure makes total credit zero; otherwise sum all three. A correct choice with success false earns 2/3. A lucky correct guess is valid; reading or reasoning traces are not graded.

Missing or malformed candidate JSON is healthy rejected work with all checks zero. Duplicate JSON keys are malformed. Unsafe/unreadable/nonregular/oversized inputs or invalid/nonunique task instances are operational errors, not scores. An error emits checks with null credit and no reward. Root verification removes stale reward JSON/text and replaces output inodes before writing current checks and normalized reward; Harbor consumes current `/logs/verifier/reward.txt`. This mechanism requires native permission and consumption verification before qualification. No runtime result is claimed by this template.

This exposed development task supplements, never replaces, the original refund task B. It is not held-out evidence and inherits no B approvals.
