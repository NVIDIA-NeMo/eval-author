<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Upstream reuse and execution choices

## Source authority

Use the maintained copy at `../vendor/taste/`, identified by its provenance
record and retained upstream license. The external research checkout is
historical evidence. Read [prepared inputs and EA execution](backend-adapters.md)
before preparing inputs. Follow the [Stage 3 protocol](stage3-protocol.md) for
skill-directed generation; an EA task service is optional automation. Source paths in the
reuse map are relative to the bundled root.

Use the [TASTE paper](https://arxiv.org/html/2605.28556v2) alongside the selected
checkout. Report paper, implementation and local adapter contracts separately.
A source map is inspection evidence, not proof of execution.

## Reuse map

| Responsibility | Maintained TASTE implementation | Prepared data or EA responsibility |
| --- | --- | --- |
| Input configuration | `src/common/domain_config.py` | Normalized seeds, policy, tool metadata, task/fixture schemas and context |
| Model calls/prompts | `src/common/call_to_llm.py`, `prompt_manager.py`, `llm_response_parser.py` | Selected transport/settings and reviewed data/overrides |
| Adaptive training | `src/stage1_ngram/initialize_ngram_model.py`; `src/common/sampler/action_sequence_ngram_sampler.py` | Lossless seed preparation; retain validity feedback |
| Contrastive sampling | `src/common/sampler/adaptive_ngram_model.py` | No duplicate EA implementation |
| Length draws | `src/common/sampler/length_distribution.py` | Selected policy and disclosed zero-call treatment |
| Sequence validity | `src/stage2_cluster/action_sequence_validator.py` | Policy and tool context; this LLM check does not execute tools |
| Pool/representatives | `src/stage2_cluster/cluster_and_validate.py`; `src/common/sampler/action_sequence_clusterer.py` | Audit-relevance mapping; retain medoids and recovery |
| Sequence distance | `src/common/sampler/sequence_metrics.py` | Prepared tool types and optional semantic groups |
| Candidate/fixture generation | Shared `artifacts/prompts/stage3/`; optional `src/stage3_tasks/task_generation.py` | Codex authors the two steps in skill mode using native definitions/examples |
| Hints | `src/common/partial_coverage_hints.py`, exposed by `scripts/taste_protocol.py` in the skill | EA/native runner consumes the hint text privately in the actual interaction model |
| Validation handoff | Optional `src/stage3_tasks/task_validator.py`; `src/backends/contracts.py` | Codex uses EA/native checks, synthesis/alignment, execution and grading directly, or the optional service |
| Patch/regeneration | Protocol preserves `src/stage3_tasks/validation_with_retry.py` ordering and limits | Codex follows actual feedback and repair scope; scripted executor remains available |
| Cluster recovery | `select_recovery_representatives` in `src/stage3_tasks/generate_tasks.py`, exposed by the protocol helper | Retain effective successful sequences, replacement lineage and total ceiling |
| Task records/export | `atomic_write_text` in `src/stage3_tasks/task_set.py`; optional scripted task-set manager | Codex persists evidence and packages through EA/native tools; scripted mode calls service export |
| EA audit/acceptance | Existing EA skills/helpers | Independent qualification, primary execution and coverage |

Benchmark-specific implementations and their assets live in the sibling
`eval-author/scripts/benchmark_runtime/` package. No native benchmark implementation
or bundled domain catalog belongs in `vendor/taste/`. Historical Tau2 generation
helpers are reused by the EA task service where applicable, not imported by
TASTE's sequence stages.

## Compatibility evidence

The [prepared-input reference](backend-adapters.md) owns shared compatibility and
readiness requirements. Source inspection or a configuration file cannot prove
runtime readiness. Preserve all rejected candidates, repair errors and lineage;
a recovered representative still needs complete task validation.

## Hinted verifier: distinct contracts

| Contract | Redaction | Attempts and execution |
| --- | --- | --- |
| Paper description | Remove a fraction `p` of argument fields from each call; shuffle calls | Verify the specified simulation and success rules before claiming reproduction |
| Selected implementation | Inspect whether it masks argument fields per call or all values for a selected fraction of actions, how rounding works and whether it shuffles | Record actual attempt, retry and success aggregation rules from the selected code |

A numeric hint parameter does not establish equivalent masking semantics.
Retain the selected hint and retry semantics when EA invokes native
simulation. The runner must consume the TASTE hints under the stated interaction
and attempt contract. Retain exact private hints, RNG settings and per-attempt outcomes.
Generation, feasibility and primary target models/budgets are separate roles.

## Upstream command composition

Use the bundled wrapper for Stages 1 and 2 so prepared-input identity checks
apply consistently. Variables identify the selected Python, skill, prepared
configuration, private workspace and run settings. These commands do not
authorize model calls.

```bash
"$TASTE_PYTHON" "$TASTE_SKILL/scripts/taste_runtime.py" run \
  --backend-config "$PREPARED_CONFIG" --workspace "$RUN_DIR" --stage train -- \
  --num-samples "$TRAINING_DRAWS" --save-dir "$TRAINING_DIR" \
  --validation-model "$SEQUENCE_MODEL" --length-config "$LENGTH_CONFIG"

"$TASTE_PYTHON" "$TASTE_SKILL/scripts/taste_runtime.py" run \
  --backend-config "$PREPARED_CONFIG" --workspace "$RUN_DIR" --stage cluster -- \
  --checkpoint "$CHECKPOINT" -k "$TASK_COUNT" --pool-size "$POOL_SIZE" \
  --output "$CLUSTERS" --validation-model "$SEQUENCE_MODEL"
```

Continue with the [Stage 3 protocol](stage3-protocol.md) by default. It owns the
hint/recovery helper commands and EA handoffs; no `--stage generate` call or
task-service import is needed for that path.

### Optional scripted Stage 3

For an automated batch with a proven configured service, include the sibling EA
scripts directory on `PYTHONPATH`. `EA_SCRIPTS` is its absolute path. This runner
performs the model calls and the retry loop instead of Codex.

```bash
PYTHONPATH="$EA_SCRIPTS" "$TASTE_PYTHON" "$TASTE_SKILL/scripts/taste_runtime.py" run \
  --backend-config "$PREPARED_CONFIG" --workspace "$RUN_DIR" --stage generate -- \
  --clusters "$CLUSTERS" --name "$TASK_SET" --model-name "$GENERATION_MODEL" \
  --gt-llm "$VERIFIER_MODEL" --max-task-retries "$TASK_RETRIES" \
  --max-patch-attempts "$PATCH_ATTEMPTS" --gt-coverage-p "$HINT_P"
```

Explicitly selected checkpoint length policies propagate unless overridden.
Unspecified length policies retain the stage defaults. Legacy native-backend
artifacts must be regenerated from a prepared input bundle.
Inspect inner retry, reclustering and model settings against the total run
ceiling. A `k`-task target is not an unlimited generation budget.

## Minimal EA handoff

Keep private working material under `.eval-author/.working/taste/<run-id>/`.
Finished tasks retain stable EA paths; native evidence stays in retained
job/measurement directories. These contents are not a second workflow authority.

- Run contract, source splits and audit-to-generation map
- Upstream revision and adapter inventory: imported component, incompatibility,
  local seam, semantic delta and proof status
- Seed/length statistics, model checkpoints, validity feedback and pool
- Medoid/replacement history and generated task lineage
- All generation, patch, hinted-check, failure and cost receipts
- Effective fixture/request/reference, exported data and parity evidence
- Native qualification and genuine primary runs with conversion receipts
- Coverage/diversity report and requested-versus-delivered reconciliation

Keep the [sequence evidence journals](backend-adapters.md#evidence-and-reuse)
alongside these artifacts; final checkpoints and selected representatives alone
omit failed attempts and superseded recovery rounds.

Mark implementation components reused, adapted, unproven or unsupported. A
source map or written plan cannot establish an adapter's runtime readiness.
