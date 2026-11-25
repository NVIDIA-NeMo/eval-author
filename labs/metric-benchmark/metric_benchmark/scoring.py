# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Explicit run selection and paired, equally weighted mutant effects."""

import math
from pathlib import Path
from statistics import fmean
from typing import Literal

from pydantic import Field

from metric_benchmark.config import LoadedAnalysis, LoadedCase, ScoringConfig, load_analysis
from metric_benchmark.io import digest_bytes, json_bytes, read_json
from metric_benchmark.models import (
    Digest,
    Execution,
    ExecutionManifest,
    Model,
    RawResults,
    Score,
    Subsets,
    Tasks,
    Text,
    resolve_evidence,
)
from metric_benchmark.validation import read_case_index, selection_for_results


class Context(Model):
    """Shared input identities used to match coverage and mutant-score artifacts."""

    run_id: Text
    case_id: Text
    baseline_bundle_id: Text
    raw_results_sha256: Digest
    subsets_sha256: Digest
    selection_sha256: Digest


def identity(value: Model) -> str:
    """Hash canonical model content so artifact identity is independent of JSON formatting."""
    return digest_bytes(json_bytes(value))


def context(raw: RawResults, subsets: Subsets, config: ScoringConfig) -> Context:
    """Bind a case's scores to the saved observations, fixed subsets, and scoring selection."""
    return Context(
        run_id=raw.run_id,
        case_id=config.case_id,
        baseline_bundle_id=config.baseline_bundle_id,
        raw_results_sha256=identity(raw),
        subsets_sha256=identity(subsets),
        selection_sha256=identity(config),
    )


def select_executions(raw: RawResults, config: ScoringConfig) -> dict[tuple[str, str, int, int], Execution]:
    """Select latest attempts by number, never by outcome; reject ambiguous replicas."""
    groups: dict[tuple[str, str, int, int], list[Execution]] = {}
    selected_runs = set(config.run_ids.values())
    for row in raw.execution_results:
        if row.agent_run_id not in selected_runs:
            continue
        if config.run_ids.get(row.agent_bundle_id) != row.agent_run_id:
            raise ValueError("selected run belongs to a different agent")
        if row.evaluation.repetition > config.repetitions:
            raise ValueError("repetition exceeds configured count")
        key = row.agent_bundle_id, *row.evaluation.task.key(), row.evaluation.repetition
        groups.setdefault(key, []).append(row)
    selected = {}
    for key, rows in sorted(groups.items()):
        if len({r.evaluation.evaluation_id for r in rows}) != 1:
            raise ValueError("multiple evaluations for one agent/task repetition")
        if len(rows) != 1 and config.attempt_policy == "only":
            raise ValueError("multiple attempts with attempt_policy=only")
        selected[key] = max(rows, key=lambda r: r.evaluation.execution)
    return selected


class RepetitionSummary(Model):
    requested: int = Field(ge=1)
    valid: int = Field(ge=0)
    rewards: list[Score]
    mean: Score
    provisional_mean: float | None


def mean_over_repetitions(scores: list[Score]) -> Score:
    missing = [
        f"repetition {index}: {score.missing_score_reason}"
        for index, score in enumerate(scores, 1)
        if score.value is None
    ]
    if missing:
        return Score(value=None, missing_score_reason="; ".join(missing))
    return Score(value=fmean(score.value for score in scores if score.value is not None))


def summarize_tasks(
    raw: RawResults, config: ScoringConfig, selected: dict[tuple[str, str, int, int], Execution]
) -> dict[str, dict[str, RepetitionSummary]]:
    summaries: dict[str, dict[str, RepetitionSummary]] = {}
    for agent in [config.baseline_bundle_id, *config.mutant_bundle_ids]:
        summaries[agent] = {}
        for task in raw.task_inventory:
            rewards = []
            for repetition in range(1, config.repetitions + 1):
                row = selected.get((agent, *task.key(), repetition))
                if row is None:
                    score = Score(value=None, missing_score_reason="missing execution")
                elif row.status not in config.completed_statuses:
                    score = Score(value=None, missing_score_reason=f"execution status {row.status}")
                else:
                    score = row.metrics.get(config.metric, Score(value=None, missing_score_reason="metric absent"))
                rewards.append(score)
            valid = [score.value for score in rewards if score.value is not None]
            provisional = math.fsum(valid) / len(valid) if valid else None
            summaries[agent][task.task_id] = RepetitionSummary(
                requested=config.repetitions,
                valid=len(valid),
                rewards=rewards,
                mean=mean_over_repetitions(rewards),
                provisional_mean=provisional,
            )
    return summaries


class NegativeScoreDiagnostic(Model):
    """Inspection flag; never changes score availability or correlation membership."""

    code: Literal["negative_mutant_score"] = "negative_mutant_score"
    eval_subset_id: Text
    mutant_bundle_id: Text | None = None  # None identifies the aggregate subset score.
    value: float = Field(lt=0)


class MutantScores(Model):
    """Validated JSON output containing subset scores, per-mutant effects, and diagnostics."""

    artifact_type: Literal["mutant_scores"] = "mutant_scores"
    context: Context
    configuration: ScoringConfig
    score_definition: Literal["mean_signed_paired_degradation_v2"] = "mean_signed_paired_degradation_v2"
    scores: dict[Text, Score]
    mutant_effects: dict[Text, dict[Text, Score]]
    task_scores: dict[Text, dict[Text, RepetitionSummary]]
    diagnostics: list[NegativeScoreDiagnostic] = Field(default_factory=list)


def compute_mutant_scores(raw: RawResults, subsets: Subsets, config: ScoringConfig) -> MutantScores:
    """Average signed paired task degradation over mutants for each fixed subset.
    Preserve negative effects; missing required pairs yield unavailable scores.
    """
    raw = RawResults.model_validate(raw.model_dump())
    subsets = Subsets.model_validate(subsets.model_dump())
    config = ScoringConfig.model_validate(config.model_dump())
    ctx = context(raw, subsets, config)
    selected = select_executions(raw, config)
    memberships = subsets.resolve_tasks(Tasks(tasks=raw.task_inventory))
    task_scores = summarize_tasks(raw, config, selected)
    effects: dict[str, dict[str, Score]] = {}
    scores = {}
    for subset in sorted(subsets.eval_subsets, key=lambda s: s.eval_subset_id):
        per_mutant = {}
        for mutant in sorted(config.mutant_bundle_ids):
            differences = []
            missing = []
            for task in sorted(memberships[subset.eval_subset_id], key=lambda t: t.key()):
                values = []
                for agent in (config.baseline_bundle_id, mutant):
                    summary = task_scores[agent][task.task_id]
                    if summary.mean.value is None:
                        missing.append(f"{agent}/{task.task_id}: {summary.mean.missing_score_reason}")
                    else:
                        values.append(summary.mean.value)
                if len(values) == 2:
                    sign = 1 if config.direction == "higher_is_better" else -1
                    differences.append(sign * (values[0] - values[1]))
            per_mutant[mutant] = (
                Score(value=None, missing_score_reason="; ".join(missing))
                if missing
                else Score(value=math.fsum(differences) / len(differences))
            )
        effects[subset.eval_subset_id] = per_mutant
        available = [s.value for s in per_mutant.values() if s.value is not None]
        scores[subset.eval_subset_id] = (
            Score(value=math.fsum(available) / len(available))
            if len(available) == len(per_mutant)
            else Score(value=None, missing_score_reason="required mutant effects unavailable; see mutant_effects")
        )
    diagnostics = []
    for subset_id, score in scores.items():
        if score.value is not None and score.value < 0:
            diagnostics.append(NegativeScoreDiagnostic(eval_subset_id=subset_id, value=score.value))
        for mutant_id, effect in effects[subset_id].items():
            if effect.value is not None and effect.value < 0:
                diagnostics.append(
                    NegativeScoreDiagnostic(eval_subset_id=subset_id, mutant_bundle_id=mutant_id, value=effect.value)
                )
    return MutantScores(
        context=ctx,
        configuration=config,
        scores=scores,
        mutant_effects=effects,
        diagnostics=diagnostics,
        task_scores=task_scores,
    )


def load_score_inputs(
    cases: Path, analysis: Path, case_id: str, results_root: Path, *, allow_unsigned: bool = False
) -> tuple[LoadedAnalysis, LoadedCase, RawResults, Subsets, ScoringConfig, Path]:
    """Load and match one case's saved results, subsets, and scoring selection.
    Signed case metadata permits loading without executable task or agent sources.
    """
    loaded = load_analysis(cases, analysis, allow_unsigned=allow_unsigned)
    prepared = loaded.inputs.case(case_id)
    case = prepared.case
    if not case.mutants:
        raise ValueError(f"case {case_id}: analysis requires at least one mutant")
    manifest = ExecutionManifest.model_validate(read_json(resolve_evidence(results_root, "execution.json")))
    if manifest.cases_sha256 != loaded.inputs.identity:
        raise ValueError("execution case definitions differ from supplied cases")
    index, raw = read_case_index(results_root, manifest, case_id)
    subsets = loaded.load_subsets(case_id)
    return loaded, prepared, raw, subsets, selection_for_results(case, raw, loaded.settings.scoring), index.parent
