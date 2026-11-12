<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Optional TASTE integration

The EA TASTE route is disabled by default. Before routing from another EA skill,
read the selected run's flag with:

```bash
python <eval-author>/scripts/taste_feature.py status --run-dir <private-run-directory>
```

Use `.eval-author/.working/taste/<run-id>/` for the selected run. A missing flag
means disabled. A malformed or relocated flag is an error; do not infer approval.
Only the user's explicit TASTE selection or approval enables the route. If that
instruction already exists for this run, record it without asking again:

```bash
python <eval-author>/scripts/taste_feature.py enable \
  --run-dir <private-run-directory> --approval '<explicit user instruction>'
```

Load the [standalone TASTE skill](../../integrations/taste/SKILL.md) only after
status reports `enabled: true`. Keep its audit, cohort and execution scope.
Approval to use TASTE does not authorize model calls or extra budget beyond the
selected execution authorization. An ordinary improvement request retains the
existing EA authoring route; do not silently enable TASTE.

For a before/after debugging comparison, disable the integration using the same
run directory and repeat the request with the same selected inputs:

```bash
python <eval-author>/scripts/taste_feature.py disable --run-dir <private-run-directory>
```

A saved disabled flag overrides earlier approval. Do not re-enable it from old
conversation context; keep the ordinary EA route until the user explicitly
requests re-enabling.

Disabling preserves the recorded approval and generated artifacts but selects
the ordinary EA route. Re-enabling requires an explicit approval argument. Direct
invocation of the standalone skill is independent of this EA routing flag.
