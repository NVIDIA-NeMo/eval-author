<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Publish prepared inputs to Helix

`publish_helix_inputs.py` uploads execution artifacts for signed cases without
running evaluations. Original task directories, agent manifests and `cases_sha256`
remain unchanged. Publication has its own identity covering the prepared inputs,
resolved execution settings, packaging and mechanically derived agent targets.

## Prerequisites

- **Prepared inputs:** signed cases with accessible task and agent files, plus
  execution and publication YAML.
- **Client environment:** a separately installed Helix client and evals SDK with
  Harbor 0.24.0, which the publisher currently requires.
- **Helix access:** an authenticated context with workspace permissions to publish
  tasks/tasksets and request image builds.

## Packaging configuration

`publication.yml` covers exactly the prepared cases and agents. It cannot supply
a second agent target, model or arbitrary kwargs.

### Choose an example

The checked-in [Helix examples](../../../tests/fixtures/metric_benchmark/helix)
extend the existing synthetic benchmark fixture:

- [execution.yml](../../../tests/fixtures/metric_benchmark/helix/execution.yml)
  supplies an explicit destination and sandbox resources.
- [execution-context.yml](../../../tests/fixtures/metric_benchmark/helix/execution-context.yml)
  resolves endpoint and workspace from the authenticated context.
- [publication-builtin.yml](../../../tests/fixtures/metric_benchmark/helix/publication-builtin.yml)
  publishes the fixture's existing | Transport | Support |
| --- | --- |
| `builtin` (default) | Oracle/NOP controls, selected by the prepared agent. |
| `task_image` | Bundles copied into a staged task, with generated Dockerfile `COPY` instructions. |
| `registered` | Not yet qualified; rejected before uploads. |

Task-image packaging rejects:

- Collisions with prepared files.
- Prebuilt task images.
- Compose layouts.
- Nonempty `.dockerignore` rules.

## Commands and output

```bash
/path/to/helix/.venv/bin/python labs/metric-benchmark/publish_helix_inputs.py \
  --cases /private/experiment/cases.yml \
  --execution /private/deployment/execution.yml \
  --publication /private/deployment/publication.yml \
  --context existing-context --output /private/publication
```

- **Preview:** add `--plan-only` to construct and verify the overlay plan without
  connecting to Helix.
- **Preview directory:** use a separate output directory when live publication
  will resolve omitted destination coordinates.
- **Publication:** the entire matrix is validated and staged before uploads.
- **Image builds:** publication requests builds but does not wait for completion.

### Output files

- `publication.json`: original and derived task digests, packaging, derived targets,
  prepared case identity and resolved execution destination.
- `sources/`: unchanged snapshots of prepared tasks and agent bundles.
- `contexts/` and `archives/`: exact derived upload contexts and compressed archives.
- `receipts/`: pinned task/taskset references, source/context digests and image-build
  observations. Evolving build responses get distinct immutable observation files.
- `provenance.json`: installed client package versions (including Fabric) and the
  deployed cluster response from initial publication.
- `bindings.json`: native references and derived targets, linked to the publication
  through `publication_sha256` and to unchanged prepared cases through `cases_sha256`.

### Retries and recovery

- **Identical retries:** reuse verified snapshots and pinned resources.
- **Interrupted copies:** never publish partial local snapshots.
- **Corrupt outputs:** are rejected; recover them before resuming.
- **Changed inputs:** require a new output directory.
- **Build observations:** may grow as builds advance or failed builds are retried.
- **Remote resources:** are never silently deleted.
- **Archive metadata:** task names include permissions and directory inventory.
  Changes create a separate resource without changing prepared content identity
  or replacing an existing task.

### Python API

Library entrypoints are `plan_publication` and `publish_helix_inputs` in
`metric_benchmark.helix_publication`; the optional SDK adapter is `HelixPublisher`.
