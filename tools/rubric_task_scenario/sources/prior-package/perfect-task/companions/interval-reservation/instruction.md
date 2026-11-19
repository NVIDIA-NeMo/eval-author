<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# interval-reservation

Read `/app/case.json`. Choose the unique option whose half-open integer interval does not overlap any occupied interval. Touching endpoints are allowed. If `occupied` is null, choose null. All intervals have integer endpoints with -10000 <= start < end <= 10000; at most 128 occupied intervals.

When evidence is present, the supplied instance must have exactly one eligible option. There are 1–32 options with distinct string IDs of 1–80 characters. The instance ID and additional descriptive metadata are not answer authority. No network, credentials or external service is needed.

Write `/work/candidate.json` as exactly `{"choice": ID_OR_NULL, "success": BOOLEAN}`. The file must be a single regular file, at most 65,536 bytes, with no symlink parent or leaf and no hardlink aliases. Do not change case data, verifier files or logs. The public scorer is `/tests/verify.py` when the harness exposes it. The reference solution is an oracle-only harness artifact, not a solver input.

## Credit and safety

Three unit checks: `well_formed_choice` (exact envelope, Boolean success, choice null or a listed ID), `current_evidence_correct` (choice equals the current evidence answer), and `optional_completion` (success is true). The first two are mandatory. Any mandatory failure makes total credit zero; otherwise sum all three. A correct choice with success false earns 2/3. A lucky correct guess is valid; reading or reasoning traces are not graded.

Missing candidate files, invalid JSON syntax, duplicate keys and non-finite JSON tokens (`NaN`, `Infinity`, `-Infinity`) are healthy rejected work with all checks zero. Syntactically valid exact envelopes with Boolean success still have the named checks evaluated: an unlisted or wrong-type choice with success true retains the optional diagnostic point, but mandatory gating makes total credit zero. Invalid envelopes receive all checks zero. Unsafe/unreadable/nonregular/oversized inputs and invalid/nonunique task instances (including invalid JSON, duplicate keys or non-finite tokens in task input) are operational errors, not scores.

After the trusted output directory has been established and stale outputs removed, handled input errors produce checks with null credit and no reward, provided those checks can be written. Output setup, cleanup or write faults are harness operational failures; current checks may be unavailable and no leftover reward is trustworthy. Root verification removes stale reward JSON/text and replaces output inodes before writing current checks and normalized `/logs/verifier/reward.txt`. S-r1 requires the separately supplied host `safe_verifier:FailClosedVerifier` consumer, which rejects nonzero execution results before reading reward files; the installed stock Harbor consumer does not provide that guarantee. Native permission and consumption verification remain required before qualification. No native runtime result is claimed by this revision.

This separately revised S-r1 exposed development task supplements, never replaces, the original refund task B. Historical S evidence remains bound to its original bytes. It is not held-out evidence and inherits no B approvals.
