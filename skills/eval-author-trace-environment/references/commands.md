<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Trace-environment commands

Invoke commands using `python <skill_dir>/scripts/trace_environment.py <command>`.
Use `<command> --help` for the full argument contract. Every command prints one
JSON object and exits nonzero on contract errors.

| Command | Purpose | Key arguments |
|---|---|---|
| `init` | Create the private, gitignored task workspace | `--task-id` |
| `prepare` | Validate, bound, and scrub one canonical ATIF | `--task-dir --atif --source-kind` |
| `audit-evidence` | Inspect recorded observation coverage without inferring success | `--task-dir` |
| `review-privacy` | Record the contextual safe-ATIF review | `--reviewer-kind --note` |
| `inventory-tool-calls` / `plan-tool-call-access` / `resolve-tool-call-access` / `generate-mock-tool-calls` | Trace-derived tool-call fixtures | `--task-dir [--decisions --reviewer-kind]` |
| `check-candidate` | Read-only candidate metadata check before construction | `--task-dir` |
| `validate-task` | Lint the authored task tree with remediation hints | `--task-dir` |
| `probe` | Diagnostic NOP/Oracle run or adoption; never proof | `--task-dir --arm --results-from` |
| `record-reproducibility` | Hash the task tree and integrity scan | `--task-dir` |
| `record-run-inputs` | Bind one future Harbor job to arm and task digest | `--task-dir --arm --job-dir` |
| `record-validation` | Derive proof from retained Harbor jobs | `--task-dir --*-job-dir --harbor-version [--copy-job-dir] [--allow-aggregate-only]` |
| `record-repair` | Archive superseded proof and record one bounded repair | `--task-dir --reason-code --note` |
| `finalize` | Write the candidate decision and summary | `--task-dir --status [--human-reviewed]` |
| `check` | Verify digests, artifacts, proof, and repair ledger | `--task-dir` |
| `batch-prepare` / `batch-status` | Prepare and report a checked-in manifest | `--manifest [--root]` |
| `prepare-publication` / `review-publication` / `export` | Digest-bound publication gate and whitelist export | `--task-dir` |
| `check-runtime` | Read-only Harbor capability preflight | `[--task-dir]` |

