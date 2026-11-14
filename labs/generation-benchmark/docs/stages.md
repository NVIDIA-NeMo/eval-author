<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Experiment stages

The experiment runs five stages. Each stage has a CLI and a completion receipt.
The reference orchestrator runs subsets one after another.
The [architecture](architecture.md#flow) page has the flow diagram and the
artifact diagram. Configuration fields are in the
[benchmark format](benchmark-format.md).

A, B, Bᵢ, C, G, Gᵢ, Bᵢ⁺, H(X), D, M(X), ΔMᵢ, and Rᵢ are defined in
[notation](architecture.md#notation).

[Architecture](architecture.md) describes the adapter, images, `harbor` shim,
relay, audit render modes, and work-root paths.

## Stage contract

These rules follow the metric benchmark's
[stage contract](../../metric-benchmark/docs/stages.md#stage-contract) where the
two benchmarks share a rule: declared inputs and outputs, completion checks, a
stop on a stage error, and reuse only when the saved inputs match.

Stage 3 completes on a timeout or a non-zero exit, and its output is the tasks
that pass the gate. An H(Gᵢ) trial that fails after retries adds no trace. That
result is a generator outcome, not a stage error.

Every stage writes a receipt. The receipt contains that stage's input digests,
image IDs, argv, and output file manifest. A stage reuses an existing output
only when the receipt matches. A changed input needs a new work root.

A stage's output is immutable. A later stage reads that output, or a copy of
it, and writes only under its own directory. Copying a file into a new
container or into the later directory leaves the source in place. The later
stage takes the earlier receipt's file list as its input. It does not scan or
rewrite the earlier tree.

The runner stops on a stage error. Per-subset failures become unavailable
values with reasons, not stage errors. A rerun reuses an unavailable
result whose receipt matches. To retry that result, delete that subset's
directory.

Stage 1 keeps a separate receipt for the M image, for H(B), for the audit, and
for M(B), and reuses each product when its own receipt matches. A later stage
is stale when a stage 1 product it reads has changed. From stage 2 on, a stale
stage makes every following stage stale.

## Stage 1: baseline

**Purpose:** Pin the measurement environment, freeze D once, and measure the full inventory.

**Inputs:** The M base, the M eval-author pin, ETHOS, the items file in
`items_file` mode, `items_from`, the signed case, agent A, the
adapter, and `harbor.retries`. ETHOS is an input in both modes, because
`generate.py` requires `--ethos`. The items file is an input only in
`items_file` mode.

**Operation:**

1. Build the M image.
2. Run H(B) and, when `items_from` is `items_file`, render the audit. The stage
   CLI builds one Harbor job for A over B, with `jobs_dir` at
   `execution/agents/agent-0000` and `job_name` `run-0001`, and the adapter runs
   it there at `n_concurrent_trials`. Harbor retries exceptions up to
   `harbor.retries` times. The tree in `execution/` follows the metric
   benchmark's [results format](../../metric-benchmark/docs/results-format.md).
   Validate it with that benchmark's validator. The tree stays where Harbor
   wrote it. Rendering runs in the M image with `--network none`. The render command is
   `generate.py --ethos <ETHOS> --items <items> --mode replace --items-mode full`.
   The two steps may run together, because the items file does not depend on H(B).
3. When `items_from` is `baseline_traces`, write one `tool` item per distinct
   tool name in the H(B) ATIF traces to
   `/workspace/.eval-author/.working/derived-items.yaml`. Each item sets
   `kind`, `name`, and one `tool_call` evidence entry for that name, and fills
   `description`, `expected_use`, and `expected_failure_behavior` with
   placeholders. Render that file with the same `generate.py` command, ETHOS,
   and `--network none`. Exactly one of the pinned items file and this derived
   file is the `--items` input. See
   [audit rendering](architecture.md#audit-rendering).
4. Freeze `.eval-author/` plus ETHOS as `audit.tar`. In `baseline_traces` mode,
   also copy the derived items file to `stage-1/derived-items.yaml`.
5. Run M(B) in a fresh M container with D and every baseline trace.

**Outputs:** `stage-1/images.json`, `stage-1/audit.tar` and its digest,
`execution/`, `coverage-full/` (the report tree and the score), and a receipt
for each of the image, H(B), the audit, and M(B). In `baseline_traces` mode,
also `stage-1/derived-items.yaml`. The audit receipt pins that file's digest
and the H(B) digest.

**Completion:** The image builds. `load_audit_spec` accepts the audit, each
`sources[].sha256` matches, and every item is a `tool` item. Every inventory
task has a validated, succeeded row. A task that still fails after retries
fails the stage, as in the metric benchmark's coverage stage.

## Stage 2: subset baseline

**Purpose:** Produce each subset's starting coverage and the before-tree G will
read.

**Inputs:** The subset manifest, `execution/`, D, and the M image.

**Operation:** Select H(Bᵢ) from `execution/` by the subset manifest's task
IDs. Copy those trials into the stage work directory. Leave `execution/`
unchanged. There is no Harbor rerun. Run M(Bᵢ) in a fresh M container with D
and those copies.

**Outputs:** `subsets/<i>/before/`, the sealed measurement workspace at the M
container `/workspace` layout, and the score. `execution/` remains the H(B)
evidence.

**Completion:** Each subset has a score. A missing slice is a stage error,
because stage 1 guaranteed that every task succeeded.

## Stage 3: generate

**Purpose:** Run G on one subset in isolation.

**Inputs:** The G base definition, the G eval-author pin, `harbor_version`,
`audit.tar`, the Bᵢ task trees, H(Bᵢ), `subsets/<i>/before/`, A's source tree,
the generation request, and the in-container argv and timeout.

**Operation:** Build the G base once. Then, for each subset:

1. Build the subset image.
2. Open a G session with a scoped token. The session key is the subset
   directory. The adapter runs G's Harbor jobs in its scratch directory.
3. Resolve the provider IP addresses and start the relay.
4. Run G. On timeout, stop the container.
5. Extract `/output/` to `subsets/<i>/generated/` and `/logs/` to
   `subsets/<i>/generation-logs/`, whatever G's exit status was.
6. Close the session. The adapter revokes the token and returns after that
   session's jobs have stopped. Copy the unscrubbed scratch tree to
   `subsets/<i>/generation-harbor/`. The files in that copy still name the
   scratch paths.
7. Apply the per-task gate.

A rerun opens the same session key, deletes the session that comes back, stops
that subset's G container and relay, and starts fresh.

**Outputs:** Gᵢ, the logs, and a receipt. The receipt includes the image ID,
argv, exit status, timeout flag, attempts summary (Accepted and Draft), the Gᵢ
tree digest, and the gate results. Accepted and Draft are diagnostics in that
summary. The gate does not remove a directory because of that status.

**Budget:** Every actionable gap from `select` gets one attempt. An optional
cap is a run flag recorded in the receipt. The cap is not part of the benchmark
definition.

**Per-task gate:** The gate is structural, not skill acceptance. A directory
under `/output/` is kept only when all of the following hold.

- It holds exactly one task, with `instruction.md`, `task.toml`,
  `environment/Dockerfile`, `tests/test.sh`, `solution/solve.sh`, and
  `README.md`.
- The last component of `[task].name` equals the directory name.
- The directory name is a single safe path component.
- The directory name is not a Bᵢ ID.
- It passes the adapter's bundle validation.

Rejected directories are listed in the receipt with reasons. The kept
directories are Gᵢ.

**Partial runs:** After a timeout or a non-zero exit, the tasks already under
`/output/` that pass the gate are Gᵢ, and stage 4 measures them. The receipt
marks the run `partial`, with the exit status, the timeout flag, and the gaps
that were never attempted. Partial subsets stay in the headline. Stage 5
reports how many there were.

**Completion:** Each subset either has an extracted Gᵢ, possibly empty, or is
unavailable with a reason. Unavailable means the image build or the extraction
failed. An empty Gᵢ is valid, including when the gate rejects every extracted
directory. Stage 4 computes Rᵢ from unchanged evidence.

## Stage 4: measure generated tasks

**Purpose:** Measure what Gᵢ adds.

**Inputs:** Gᵢ, the subset manifest, `execution/`, D, the M image, and A.

**Operation:** For each subset whose Gᵢ was extracted:

1. The stage CLI builds one Harbor job for A over Gᵢ, with tasks at
   `tasks/<task_id>`, A's inputs, and `jobs_dir` at
   `subsets/<i>/execution/agents/agent-0000`. The adapter runs it there at
   `n_concurrent_trials`. Leave the stage 1 `execution/` unchanged. The new tree
   stays where Harbor wrote it.
2. Select H(Bᵢ) again from `execution/` by the subset manifest. Run M(Bᵢ⁺) in
   a fresh M container with copies of D, those trials, and H(Gᵢ). Leave
   `execution/`, `subsets/<i>/before/`, and `subsets/<i>/execution/` unchanged.
   The copies come from those trees, never from the G container or from
   `before/`.
3. Compute ΔMᵢ and Rᵢ. The receipt lists the Bᵢ and Gᵢ paths and digests. The
   case task list stays unchanged.

**Outputs:** `subsets/<i>/after/` and the per-subset values with reasons.

**Completion:** Each subset has values or an unavailable reason. An H(Gᵢ) task
that fails after retries is recorded and contributes no trace. That failure is
a generator outcome, not a stage error.

## Stage 5: analysis

**Purpose:** Produce the final artifact, modeled on the metric benchmark's
[correlation stage](../../metric-benchmark/docs/stages.md#4-correlation).

**Inputs:**

- M(B) and `items_from`.
- Each subset's M(Bᵢ).
- The stage 4 values M(Bᵢ⁺), ΔMᵢ, and Rᵢ, or the reason any of those values is missing.
- The tool names recorded in the M(B) and M(Bᵢ⁺) measurement details.
- The stage 3 partial-run mark.

**Operation:** Read the saved stage receipts and scores. Collect the tool
names in those details. Write only `generation-analysis.json`. Compute the
headline, the counts below, and `tools_outside_denominator`. A failed
assertion fails the stage.

**Outputs:** `generation-analysis.json` contains the following.

- Per subset: M(Bᵢ), M(Bᵢ⁺), ΔMᵢ, Rᵢ, and the reason for any missing value.
- M(B).
- `items_from`, because it changes what R can mean. In `items_file` mode, an
  Rᵢ above 1 means Gᵢ covered an item that B does not cover. The gap
  M(B) − M(Bᵢ) does not count that item. What `baseline_traces` changes about
  R is in [audit rendering](architecture.md#audit-rendering).
- The headline: the median of every defined Rᵢ, including Rᵢ > 1.
- The raw ΔMᵢ values next to the headline, because R is quantized when D is
  small.
- Counts of `included`, `partial` (a subset of `included`), `zero_gap`, and
  `unavailable`. `zero_gap` means Rᵢ is undefined because M(B) = M(Bᵢ).
- `tools_outside_denominator`: the sorted tool names in the M(B) or M(Bᵢ⁺)
  details that are absent from D. The list may be empty. M and R stay as
  computed from D. In `baseline_traces`, H(B) cannot contribute a name, so
  every name comes from H(Gᵢ).

`zero_gap` and `unavailable` stay out of the headline.

When `tools_outside_denominator` is not empty, the stage CLI prints:

```text
Coverage exceeded the denominator. Rerun with the new items included?
```

The reference orchestrator prints the same line after stage 5. The stage still
completes. Counting those tools on the next run means adding them to the items
file. `baseline_traces` builds D from H(B) alone, so that same derivation
leaves the names out.

**Assertions:** Coverage is the union of items covered by the traces in that
one measurement, and subsets are never merged. The two results below are
broken evidence. Each one fails the stage instead of being counted.

- M(Bᵢ) > M(B). H(Bᵢ) is a slice of H(B) under the same frozen D, so the
  subset cannot cover an item the full run missed.
- M(Bᵢ⁺) < M(Bᵢ). M(Bᵢ⁺) is measured on that same slice plus H(Gᵢ), so the
  covered set cannot shrink.

**Completion:** Every subset appears exactly once.
