<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# correlate.py

Correlate paired coverage and mutant scores across task subsets.

## Usage

```bash
uv run python labs/metric-benchmark/correlate.py \
  --coverage coverage-scores.json --mutant-scores mutant-scores.json \
  --output correlation.json
```

## Inputs and behavior

Artifacts must match in source identity, selection and subset IDs. Correlation
uses Spearman ranks with average ties. Constant ranks or fewer than two usable
pairs give an explicit undefined coefficient. `--missing-policy exclude` is an
opt-in that records excluded pairs and their reasons. The default rejects
unavailable pairs. This coefficient is descriptive: overlapping subsets do not
provide independent samples, and the tool does not report significance tests.

With exactly two usable pairs and no ties on either axis, the coefficient is
necessarily +1 or -1; inspect the included pair count before interpreting it.
Analysis artifact digests identify canonical model content, whereas prepared-file
pins identify exact file bytes (including formatting).

## Python library

`correlate(coverage, mutant_scores, missing_policy="error")`.
