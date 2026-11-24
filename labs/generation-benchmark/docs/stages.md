<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Experiment stages

`run_generation_experiment.py` runs each implemented stage in order. The diagram is on the [architecture](architecture.md#flow) page. Configuration fields are in the [benchmark format](benchmark-format.md). Symbols are in [notation](architecture.md#notation), and `Gᵢ` is defined in [generated tasks](architecture.md#generated-tasks).

## Stage contract

Stages follow the metric benchmark's [stage contract](../../metric-benchmark/docs/stages.md#stage-contract): declared inputs and outputs, completion checks, and reuse only when the saved inputs match. A subset failure becomes an `unavailable` value with a reason. A failed generator run or Harbor trial is an outcome, not a stage error.

Each stage writes a stage receipt: the digests of the lock entries and upstream receipts it read. A stage records only the entries it reads, so a change to the `generation` block reruns stage 3 and later but not stages 0 to 2. A matching stage receipt skips the stage. The check reads receipt files and hashes the pinned inputs. It does not rehash Harbor trial trees.

A Harbor job receipt is the job's request digest. The job is reused when it matches, and a finished job whose files have changed fails.

A metric-command receipt records the command, the block's `digest` entry when declared, the `metric-setup/` digest when setup is declared, the trial set, and the score. The command reuses its result when those match.

The generation receipt is defined in the [generator contract](architecture.md#generator-contract).

A stage checks the digest of each published output it reads. A published output that no longer matches its receipt is a stage error that names the directory. The stage does not rerun it, and the user invalidates that stage. A mismatch in a lock entry or an upstream receipt reruns that stage in the same work root and replaces its output. Later stages see a new upstream digest and rerun. Earlier stages with matching receipts are skipped. A different `--run-id` needs a new work root. A stage's output is immutable while its receipt matches, and a later stage reads it and writes only its own directory. A rerun reuses an unavailable result whose receipt matches.

Stages that write user-command output also check it against the declared type: `metric-setup/` after stage 0 when setup is declared, the metric output after stages 1, 2, and 4, and the stage 2 metric output before `G` runs. See [metric contracts](architecture.md#metric-contracts).

A change to Python under `labs/generation-benchmark/` changes the generation receipt through the implementation digest, so stage 3 and later rerun. It does not change the receipts of stages 0 to 2. A change to a command's own code changes its stage's receipt only when that command declares a `digest`. Otherwise, invalidate the stage by hand.

`--invalidate-stage STAGE` removes that stage's directory and prints a warning that names the directory and the later stages that rerun.

| Stage | `--invalidate-stage` | Directories removed |
| --- | --- | --- |
| 0. Metric setup | `setup` | `cases/case-NNNN/metric-setup/` |
| 1. Baseline | `baseline` | `cases/case-NNNN/execution/`, `metric-full/` |
| 2. Subset baseline | `subsets` | `cases/case-NNNN/metric-subsets/` |
| 3. Generate | `generate` | `cases/case-NNNN/generation/` |
| 4. Run and measure | `measure` | `cases/case-NNNN/execution-gen/`, `metric-plus/` |
| 5. Analysis | `analysis` | `generation-analysis.json` |

`--case CASE_ID` runs stages 0 to 4 for that case and leaves other cases untouched. An unknown id is an error before any stage runs. Stage 5 does not run, because its headline is the median across all cases. `--invalidate-stage` with `--case` removes only that case's directories. `--invalidate-stage analysis` with `--case` is an error.

## Stage 0: metric setup

**Purpose.** Prepare `D` in `metric-setup/` when `metric.setup` is declared.

**Inputs.** The experiment config, the lock, and `metric.setup.command` when declared.

**Operation.** Check config schemas, and that the locked files exist and match the lock. A failed check is a stage error. Then run `metric.setup.command` if declared. See [metric setup](architecture.md#metric-setup).

**Outputs.** `metric-setup/`, if declared. That directory is `D`.

**Completion.** The input check passes. If setup is declared, `metric.setup.command` exits 0 and `metric-setup/` loads as `metric.setup.output`.

## Stage 1: baseline

**Purpose.** Run `H(B)` and measure `M(B)`.

**Inputs.** The locked case, the `execution` block, and `metric-setup/` when declared.

**Operation.**

1. Run `H(B)`. Write the trials under `execution/agents/agent/`. `results.json` is at `execution/results.json`.
2. Measure `M(B)` from those trials and `metric-setup/`, when declared. Write the score and the metric output to `metric-full/output/`.

**Outputs.** `execution/` and `metric-full/`.

**Completion.** Every inventory task has a succeeded row and an `agent/trajectory.json`. A task that lacks either fails the stage and is listed by id. The metric command wrote `score.json`, and its output loads as `metric.score.output`.

## Stage 2: subset baseline

**Purpose.** Measure `M(Bᵢ)` for each subset from the stage 1 trials.

**Inputs.** The case's subset manifest, `execution/`, and `metric-setup/` when declared.

**Operation.** For each subset, select the rows whose task id is in `Bᵢ`. Measure `M(Bᵢ)` on those rows and the same `metric-setup/` when declared, with the same metric command as stage 1. The command writes its output under `metric-subsets/subset-NNNNNN/output/`.

**Outputs.** `metric-subsets/`: one result per subset, in `subset-NNNNNN/` order of sorted subset id. Stage 3 stages that output.

**Completion.** Every subset has a score or an explicit unavailable value. A subset whose score is null, or whose output does not load as `metric.score.output`, is `unavailable` in stage 5.

## Stage 3: generate

**Purpose.** Run `G` on one subset in isolation.

**Inputs.** `M(B)` from `metric-full/` and `M(Bᵢ)` from `metric-subsets/`. `Bᵢ`'s task directories and `A`'s source from the locked case. ETHOS from the case's `ethos` reference. The `generation` settings.

**Operation.** For each subset, in `subset-NNNNNN` order:

1. When `M(B) = M(Bᵢ)`, record `zero_gap` and do not launch `G`.
2. Stage the workspace with the agent, ETHOS, and `Bᵢ`. The request names the subset's metric output directory. Check that it loads as `metric.score.output`. Write `request.json` and run the [generator command](architecture.md#generator-contract).

**Outputs.** `generation/<subset-dir>/`. The directories under `output/tasks/` are `Gᵢ`. The generation receipt is defined under the [generator contract](architecture.md#generator-contract).

**Budget.** `--max-gaps` is a run flag recorded on the request. It is not part of the benchmark definition. The command decides how many gaps to attempt.

**Partial runs.** The stage completes on a timeout or a non-zero exit. The directories already under `output/tasks/` go to stage 4, and the receipt marks the run `partial`. Partial subsets stay in the headline. Stage 5 reports how many there were.

**Completion.** Each subset is `zero_gap`, has a task list (possibly empty), or is `unavailable` with a reason. Unavailable means staging or the launch failed. An empty list is valid. Stage 4 then sets `M(Bᵢ⁺)` from unchanged evidence.

## Stage 4: run and measure

**Purpose.** Run `Gᵢ` for the first time, record the outcomes, then measure `M(Bᵢ⁺)`.

**Inputs.** The directories under `output/tasks/`, `agent.yml`, the `execution` block, `execution/`, and `metric-setup/` when declared.

**Operation.** For each subset whose `Gᵢ` is not empty:

1. Run the structural check on each directory. Record `structural_failed` when any of the following fail. The directory stays in `Gᵢ`.
   - It contains `instruction.md`, `task.toml`, `environment/Dockerfile`, `tests/test.sh`, `solution/solve.sh`, and `README.md`.
   - The directory name matches `^[A-Za-z0-9][A-Za-z0-9._-]*$`.
   - The directory name equals the last `/`-separated component of `[task].name`.
   - The directory name is not a task id in `Bᵢ`. The recorded rule is `id_collision`.
   - The bundle has no compose file, no `${…}` template, no `mcp_servers`, and no prebuilt `docker_image`.
2. Run three host Harbor jobs on `Gᵢ`. `A` writes `agents/agent/`, `oracle` writes `agents/oracle/`, and `nop` writes `agents/nop/`, under `execution-gen/<subset-dir>/execution/`.
3. Record each task's outcomes. A failed check stays in `Gᵢ`.

   | Recorded outcome | When |
   | --- | --- |
   | `structural_failed` | the structural check failed |
   | `oracle_failed` | `oracle` reward is not 1.0 |
   | `nop_passed` | `nop` reward is not 0.0 |
   | `nop_no_reward` | `nop` recorded no reward |
   | `agent_error` | `A`'s trial did not succeed |
   | `agent_no_trace` | `A`'s trial directory has no `agent/trajectory.json` |

4. Measure `M(Bᵢ⁺)`. The task list is `Bᵢ` plus `Gᵢ`. The trials are the stage 1 rows for `Bᵢ` and the stage 4 rows for `Gᵢ`. The metric inputs are `metric-setup/`, when declared. The command reads that directory in place. Write the score under `metric-plus/<subset-dir>/output/`.

When `Gᵢ` is empty, do not measure `M(Bᵢ⁺)`. Record `M(Bᵢ⁺)` = `M(Bᵢ)` and `m_after_source: "unchanged"`.

**Outputs.** `execution-gen/<subset-dir>/`, including the per-task outcomes, and `metric-plus/<subset-dir>/`.

**Completion.** Every included subset has `M(Bᵢ⁺)` or an unavailable reason. A `metric-plus/` output that does not load as `metric.score.output` is an unavailable reason.

## Stage 5: analysis

**Purpose.** Compute `ΔMᵢ` and `Rᵢ`, and write `generation-analysis.json`. `ΔMᵢ` is `M(Bᵢ⁺) − M(Bᵢ)`. `Rᵢ = ΔMᵢ / (M(B) − M(Bᵢ))`.

**Inputs.**

- `M(B)`, from `metric-full/`.
- Each subset's `M(Bᵢ)`, from `metric-subsets/`.
- The stage 4 values `M(Bᵢ⁺)`, `ΔMᵢ`, and `Rᵢ`, or the reason a value is missing.
- The stage 3 partial-run mark.
- The metric command identity recorded on each measurement receipt.

**Operation.** Read the saved receipts and scores. Write only `generation-analysis.json`. Compute the headline and the counts below.

**Outputs.** `generation-analysis.json` contains the following.

- Per subset: status `included`, `zero_gap`, or `unavailable`; whether the generation run was partial; `M(Bᵢ)`, `M(Bᵢ⁺)`, `ΔMᵢ`, `Rᵢ`; `m_after_source` `measured` or `unchanged`; and the reason for any missing value.
- `M(B)`. An `Rᵢ` above 1 means `Gᵢ` covered an item that `B` does not cover.
- The headline: the median of every defined `Rᵢ`, including `Rᵢ > 1`, and the count of defined values. The raw `ΔMᵢ` values sit beside the headline, because `R` is quantized when `D` is small.
- Counts of `included`, `partial` (a subset of `included`), `zero_gap`, and `unavailable`.
- Per subset, the quality counts for `Gᵢ`: `structural_failed`, `oracle_failed`, `nop_passed`, `nop_no_reward`, `agent_error`, and `agent_no_trace`.

`zero_gap` means `M(B) = M(Bᵢ)`, so `Rᵢ` is null. `zero_gap` and `unavailable` stay out of the headline.

**Check.** Stage 5 fails when the measurements in a case did not all use the same metric. Every measurement receipt must name the same metric scoring command, and the same `metric-setup/` when setup is declared.

**Completion.** Every subset appears exactly once.
