<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Execute a published Helix benchmark

`helix_execute.py` runs one selected agent against a case's pinned taskset.
It reads `references.json`, `execution.yml`, and the shared `--client` file.
Original source directories are not required. See the [architecture examples](helix-architecture.md)
for baseline and mutant commands and the [publication guide](publish-helix-inputs.md)
for client configuration and source retention.

Execute once per selected baseline/mutant. Save each run to its own `--output` JSON
receipt. `--plan-only` writes the exact request and retry key without connecting to
Helix. Use that same output to submit it. `--run-id` optionally supplies an explicit
run identity; otherwise a new receipt gets a generated identity.

Before submission, execution requests missing task image builds, waits for readiness,
checks the registered agent configuration and files, and verifies task/taskset pins.
`--build-timeout` bounds the wait (default 1800 seconds). Pending builds return exit 2;
retry the same receipt. A failed build or identity mismatch returns exit 1.
Publication itself never builds images. Changing the agent does not rebuild tasks.

The run receipt saves the selected request and idempotency key **before** the POST.
A lost response can be recovered by repeating the identical command. An accepted
receipt reuses its remote run; a different request cannot overwrite it. An intentional
repeat uses a new output file and run identity. Collection explicitly selects which
receipt to use for each agent; repeated trials are not silently combined.

Receipts preserve the publication references, destination, compute settings, native
acceptance response, client/server versions, image-build observations, and captured
resolved configuration/source hashes as available. These records are private evidence;
keep them outside public checkouts. `helix_sources.py --run` can recover the retained
sources even after registration is replaced.

After every selected run finishes, use `helix_collect.py` from the collection PR to
write the shared results directory. There is no separate import command.
