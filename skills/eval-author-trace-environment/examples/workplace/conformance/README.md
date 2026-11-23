<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Workplace source conformance

- Compare newly executed synthetic observations from pinned [Gym source](https://github.com/NVIDIA-NeMo/Gym/blob/0c5cfb71dd8615008fc69b63cdc98c2775278aa5/resources_servers/workplace_assistant/workplace_assistant_tools/email.py) with exact replay and the reconstructed HTTP service.
- Keep the complete `conformance/` directory outside agent images and task environments. Backend builders may read only `corpus-v1/development/` and `corpus-v1/schemas.json`.
- This is not the requested Oct30 corpus and is not evidence from a live user service.

## Run

From the repository root, use a Python environment with the existing repository dependencies, including `jsonschema`.

```bash
python skills/eval-author-trace-environment/examples/workplace/conformance/runner.py \
  --service skills/eval-author-trace-environment/examples/workplace/runtime/service.py \
  --scratch "$HOME/.hermes/cache/scratch/workplace-conformance-run" \
  --report skills/eval-author-trace-environment/examples/workplace/conformance/report.json
```

Omit `--service` to run exact replay only. The report then identifies the missing after comparison. The runner uses the existing `replay_mcp_server.py` scenario contract and executable, not a replacement replay implementation. It compiles cases only from development observations. Conflicting outputs for a canonical input produce no replay case. Malformed schema inputs and uncaught source exceptions are excluded from exact replay.

Each heldout session starts a separate HTTP service with its own seed array and private state directory. Calls use `POST /call` with `{name, arguments}` and consume `{output, unsupported}`. The runner reads `state.json` under that private directory after every call. Its `state` field is the persisted email array. `--state-file` selects a different filename. An adjacent `mcp_server.py` supplies the advertised schema through a real `tools/list` request.

Reports preserve every heldout call and denominator, including malformed and unsupported cases. Output values, output shape, string error or exception behavior, and after-state have separate results. `category_results` separates normal coverage from malformed and unsupported probes. Unsupported responses do not count as output matches. A nonzero exit signals unexpected supported-output mismatches, any after-state mismatch, or changed input-validation schema semantics. Missing verification remains an explicit gap. Description differences are reported separately from validation semantics.

Exact replay has no mutation mechanism. Its after-state remains each heldout session's initial seed. This exposes state divergence separately from response matching. Outputs are compared as JSON values with array order preserved. Before rates include heldout identifiers absent from development and do not measure only repeated exact requests.

## Frozen evidence

`corpus-v1/freeze.json` binds all independently seeded input sessions before any backend inspection. `provenance.json` binds the input freeze, original upstream tool schemas, executed observation files, source URL and revision, source and license hashes, Python, pandas, and execution time. The runner pins both manifest hashes. It rejects tampering, overlapping session identities, overlapping seed identifiers, split mismatches, and broken state chains.

Every split has search, read, reply, alternate-order, and malformed sessions. Seeds have six string fields, numeric string IDs, and ISO timestamps. The source clock is unchanged at `2023-11-30 23:59:00`. Only `reset_state` was overridden to construct synthetic pandas rows. Search, information lookup, reply, and reply's internal send operation execute the actual downloaded upstream class. The raw upstream Python and license remain in private scratch, not this repository. No upstream operation code is copied into the backend.

The capture used an isolated scratch environment with pandas because the repository environment lacked pandas. The conformance consumer does not require pandas. `capture.py freeze` refuses an existing freeze. `capture.py observe --scratch PATH` requires the pinned source hash and writes observation files exclusively. Recapture must use a distinct corpus version and new pinned manifest constants. Do not replace this frozen evidence in place.

`report.json` records the exact runtime file hashes tested. If sibling runtime files are still changing, that report establishes only its recorded snapshot. Confirm a stopped-editing handoff or rerun on the finalized runtime before treating it as final integration evidence. Native task grades and independently expected alternate grades are outside this runner and are tested by the task owner.
