<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Publish prepared inputs to Helix

`helix_publish.py` publishes signed agents, unchanged tasks, or both and writes
`references.json`. `helix_sources.py` recovers verified sources from these
references or a run receipt. See the [architecture workflow](helix-architecture.md)
for the four commands and the [configuration examples](../examples/helix/README.md).

## Prerequisites

Use the separate qualified Helix client environment (Harbor 0.24.0, agents/evals
plugins), signed case and agent/task manifests, and authorization to register
agents, store files and publish tasks/tasksets. Only the source directories selected
by `--resources` must exist locally. Keep client settings and generated evidence
outside public checkouts.

All four commands take `--client`. Its YAML supplies `base_url`, `workspace`, and
`auth.access_token_env`, the name of an environment variable containing a supported
access token. Alternatively, `auth.context` and optional `auth.config_path` select
an existing native Helix login context, including device-login refresh. The context
must match the configured destination. Credential values are never written into
publication references.

## Publication and source retention

Agent manifests use `helix-registered` with `bundle_input` and `config_path`;
the signed definition owns model, behavior and runtime settings. Publication
settings contain secret references and optional existing pinned task revisions.
They cannot override compute or agent behavior. Oracle/NOP controls retain their
native Harbor definitions. Local execution rejects registered agents explicitly.

Publication checks selected inputs before uploads, registers content-derived agent
names, verifies existing configurations/files, and uploads task directories without
editing Dockerfiles. It creates pinned tasksets but does not request image builds;
execution owns builds and readiness. Agent FileSets currently require text files.

Each registered agent also has a separate content-addressed `mb-sources-*` FileSet
containing `agent.json` (the signed manifest entry) and `bundle/` (the original
bundle including its definition). It is ordinary persistent file storage, with no
run/agent owner or run-snapshot TTL. Publication never deletes it or overwrites
existing files: retries upload missing files and reject changed or extra contents.
Operators must retain these FileSets for as long as references/results need source
recovery. Administrative deletion remains possible and is reported as missing
evidence, never replaced with a newer registration.

`references.json` records the prepared case definitions, content identities,
destination, publication plan, registrations and their retained source locations,
pinned tasks/tasksets, and observed client/server versions. It contains everything
execution needs without the original upload directory. Keep the experiment and
analysis configuration separately; source download does not recreate them.

Use `--references` when adding separately published resources. Conflicting mappings
fail. Repeating publication checks and reuses matching remote contents; it never
replaces differing resources. Task names also account for archive permissions and
empty directories, while the original prepared content identity remains unchanged.

Source downloads preserve the input paths relative to `cases.yml`. Existing matching
files are reused; conflicting files, changed retained definitions, archive hashes or
bundle hashes fail. A run receipt selects the agent and task versions for that run.
No secret values or Git history are downloaded.
