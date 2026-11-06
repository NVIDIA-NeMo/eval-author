<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Publish prepared inputs to Helix

`publish_helix_inputs.py` uploads execution artifacts for signed cases without
running evaluations. Original task directories, agent manifests and `cases_sha256`
remain unchanged. Publication has its own identity covering the prepared inputs,
resolved execution settings, packaging and mechanically derived agent targets.

## Prerequisites

Use a separately installed Helix client with Harbor 0.24.0 and the evals SDK; keep
it separate from the default benchmark environment (Harbor 0.20.0). The client
records installed package versions (including Fabric) and the deployed cluster response in
`provenance.json`; publication refuses an unqualified Harbor client version.
The authenticated context needs an authorized workspace, native task/taskset
publication and image-build permissions. Do not put credentials in YAML.
Keep deployment YAML, publication outputs and live evidence outside the checkout.

Local execution may explicitly select Harbor 0.24.0 in its own client environment.
This permits the same installed adapter on both backends without changing the
prepared agent. An adapter must be installed in each execution worker; arbitrary
Python import paths do not cause automatic worker package installation. Fabric
requires the task-side package to match the worker release; use the deployment's
qualified client/adapter versions. The cluster revision alone does not report
every worker package version; preserve job specifications and startup failures.

## Packaging configuration

`publication.yml` covers exactly the prepared cases and agents. It cannot supply
a second agent target, model or arbitrary kwargs:

```yaml
agents:
  example:
    baseline:
      transport: task_image
      bundle_input: runtime
      image_bundle_path: environment/benchmark-agents/baseline
      image_mount_path: /opt/benchmark-agents/baseline
      env_secrets:
        OPENAI_API_KEY: workspace/model-access
    mutant:
      transport: task_image
      bundle_input: runtime
      image_bundle_path: environment/benchmark-agents/mutant
      image_mount_path: /opt/benchmark-agents/mutant
      env_secrets:
        OPENAI_API_KEY: workspace/model-access
```

`bundle_input` names a content-pinned directory in that agent's `inputs`.
Prepared Harbor kwargs may reference it with `{input:runtime}`. The local backend
resolves that reference to the prepared filesystem path; task-image transport
resolves it to the declared image mount. Other untransported input references
are rejected. Mount paths for different agents must not overlap. A secret binding
must match a prepared `env` entry such as `OPENAI_API_KEY: '${OPENAI_API_KEY}'`.

For `FabricInstalledAgent`, the canonical prepared kwargs declare
`fabric_config_bundle: '{input:runtime}'` and `fabric_config_target` equal to the
image mount. Local execution uploads the bundle there. Helix preloads the same
bytes into the image and omits only the redundant host-side upload argument.
All other prepared configuration is retained. This mapping does not translate a
different local adapter into Fabric: prepare one adapter configuration for both.

Oracle/NOP controls use `transport: builtin` (the default), with their source
selected solely by the prepared agent. Registered-agent mappings are not yet
qualified and are rejected before uploading anything.

Task-image publication copies the original task to a staging directory, adds the
pinned bundles, and appends generated JSON-form Dockerfile COPY instructions.
It rejects collisions with prepared files, prebuilt task images, compose layouts,
and nonempty `.dockerignore` rules. These restrictions make the supported
transformation explicit; publication does not guess how arbitrary build layouts work.

## Commands and output

```bash
/path/to/helix/.venv/bin/python labs/metric-benchmark/publish_helix_inputs.py \
  --cases /private/experiment/cases.yml \
  --execution /private/deployment/execution.yml \
  --publication /private/deployment/publication.yml \
  --context existing-context --output /private/publication
```

`--plan-only` constructs and verifies the complete overlay plan without connecting
to Helix. Use a separate preview output directory when the live context will resolve
omitted destination coordinates. Publishing validates and stages the entire matrix
before the first remote mutation. It requests builds but does not wait for completion.

The output contains:

- `publication.json`: original and derived task digests, packaging, derived targets,
  prepared case identity and resolved execution destination.
- `sources/`: unchanged snapshots of prepared tasks and agent bundles.
- `contexts/` and `archives/`: exact derived upload contexts and compressed archives.
- `receipts/`: pinned task/taskset references, source/context digests and image-build
  observations. Evolving build responses get distinct immutable observation files.
- `provenance.json`: observed client/server versions from initial publication.
- `bindings.json`: native references and derived targets, linked to the publication
  through `publication_sha256` and to unchanged prepared cases through `cases_sha256`.

Identical retries reuse verified snapshots and pinned resources. Interrupted local
copies never publish partial snapshots. Corrupt saved outputs are rejected; changed
inputs require a new output directory. Build observations may grow as builds advance
or a failed build is retried. Publication never silently deletes remote resources.

Library entrypoints are `plan_publication` and `publish_helix_inputs` in
`metric_benchmark.helix_publication`; the optional SDK adapter is `HelixPublisher`.
