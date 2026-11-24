# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stages 0-3 orchestrator for generation benchmarks."""

from __future__ import annotations

import asyncio
import shutil
import sys
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal

from harbor.models.job.config import JobConfig
from harbor.models.trial.config import EnvironmentConfig
from metric_benchmark.io import digest_bytes, digest_file, json_bytes, read_json

from generation_benchmark.digests import directory_manifest, implementation_digest, implementation_info
from generation_benchmark.models import (
    GenerationInputs,
    ImplementationRecord,
    LoadedCase,
    LoadedExperiment,
    LockEntry,
    MetricCommandReceipt,
    Score,
    ScoreRequest,
    ScoreTrialEntry,
    SetupRequest,
    Stage0SetupReceipt,
    Stage1ExecutionReceipt,
    Stage1ExecutionRow,
    Stage1Results,
    SubsetsStageReceipt,
    Task,
)
from generation_benchmark.signing import ComputedLock, check_experiment, verify_lock_entries
from generation_benchmark.staging import (
    atomic_write_bytes,
    copy_tree_filtered,
    generate,
    generation_lock_keys,
    subset_dir_names,
)
from generation_benchmark.validation import (
    collect_stage1_job,
    invoke_workspace_command,
    resolve_harbor_agent_config,
    run_command_subprocess,
    validate_directory_output,
    validate_stage0_setup,
    validate_stage1_execution,
)

INVALIDATE_STAGE_CHOICES = ("setup", "baseline", "subsets", "generate")


def run_harbor(config: JobConfig) -> None:
    """Run one Harbor job on the host and wait for completion."""
    from harbor.job import Job

    async def _run() -> None:
        job = await Job.create(config)
        await job.run()

    asyncio.run(_run())


def _dir_manifest_excluding(directory: Path, excluded_names: set[str]) -> dict[str, str]:
    full = directory_manifest(directory)
    return {rel: sha for rel, sha in full.items() if rel not in excluded_names}


def _case_settings_digests(
    case_id: str,
    lc: LoadedCase,
    verified: dict[str, LockEntry],
) -> dict[str, str]:
    return {dotted: verified[f"cases[{case_id}].settings.{dotted}"].sha256 for dotted in sorted(lc.case_settings_refs)}


def _invalidate_case_stages(case_dir: Path, case_id: str, stage: str) -> None:
    if stage == "setup":
        targets = [case_dir / "metric-setup"]
        warning_tail = "setup, baseline, subsets, and generate will rerun"
    elif stage == "baseline":
        targets = [case_dir / "execution", case_dir / "metric-full"]
        warning_tail = "baseline, subsets, and generate will rerun"
    elif stage == "subsets":
        targets = [case_dir / "metric-subsets"]
        warning_tail = "subsets and generate will rerun"
    elif stage == "generate":
        targets = [case_dir / "generation"]
        warning_tail = "generate and every later stage will rerun"
    else:
        raise ValueError(f"unsupported stage for --invalidate-stage: {stage!r}")

    for target in targets:
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
    joined = ", ".join(str(t) for t in targets)
    print(
        f"warning: case {case_id}: invalidated stage {stage} (removed {joined}); {warning_tail}",
        file=sys.stderr,
    )


def _update_generation_inputs(
    inputs_path: Path,
    *,
    run_id: str,
    used_entries: dict[str, LockEntry],
) -> None:
    impl = ImplementationRecord.model_validate(implementation_info())
    merged_entries: dict[str, LockEntry] = {}
    if inputs_path.exists():
        if inputs_path.is_symlink() or not inputs_path.is_file():
            raise ValueError(f"generation-inputs.json must be a regular file: {inputs_path}")
        existing = GenerationInputs.model_validate(read_json(inputs_path))
        if existing.run_id != run_id:
            raise ValueError(
                f"work root {inputs_path.parent} belongs to run_id {existing.run_id!r}, not {run_id!r}; "
                "use a new --work-root"
            )
        merged_entries.update(existing.entries)
    merged_entries.update(used_entries)
    record = GenerationInputs(run_id=run_id, implementation=impl, entries=merged_entries)
    atomic_write_bytes(inputs_path, json_bytes(record.to_payload()))


def _run_stage0_setup(
    *,
    loaded: LoadedExperiment,
    computed: ComputedLock,
    verified: dict[str, LockEntry],
    lc: LoadedCase,
    case_dir: Path,
    kill_grace_seconds: float,
) -> tuple[Path | None, str | None]:
    exp = loaded.experiment
    case_id = lc.definition.case_id
    if exp.metric.setup is None:
        return None, None

    setup_cfg = exp.metric.setup
    setup_dir = case_dir / "metric-setup"
    case_lock_keys = sorted(
        [
            f"cases[{case_id}].agent",
            f"cases[{case_id}].tasks",
            f"cases[{case_id}].subsets",
            f"cases[{case_id}].ethos",
            *(f"cases[{case_id}].settings.{k}" for k in lc.case_settings_refs),
        ]
    )
    case_lock_sha256 = {k: verified[k].sha256 for k in case_lock_keys}

    setup_req = SetupRequest(
        case_id=case_id,
        ethos_path=str(lc.ethos_path),
        agent_path=str(lc.agent_path),
        tasks_path=str(lc.tasks_path),
        subsets_path=str(lc.subsets_path),
        settings=lc.resolved_settings,
    )
    req_bytes = json_bytes(setup_req.to_payload())
    request_sha256 = digest_bytes(req_bytes)

    assert computed.setup_identity_sha256 is not None
    assert computed.setup_output_module_sha256 is not None

    receipt_path = setup_dir / "receipt.json"
    extra_pp = [loaded.experiment_dir]

    if receipt_path.is_file():
        existing = Stage0SetupReceipt.model_validate(read_json(receipt_path))
        pins_match = (
            existing.case_id == case_id
            and existing.command == setup_cfg.command
            and existing.setup_identity_sha256 == computed.setup_identity_sha256
            and existing.setup_digest_sha256 == computed.setup_digest_sha256
            and existing.output_module_sha256 == computed.setup_output_module_sha256
            and existing.case_lock_sha256 == case_lock_sha256
            and existing.request_sha256 == request_sha256
        )
        current_files = _dir_manifest_excluding(setup_dir, {"request.json", "receipt.json"})
        if pins_match:
            if current_files != existing.files:
                raise ValueError(
                    f"case {case_id}, path {setup_dir}: published metric-setup directory changed; "
                    "use --invalidate-stage setup"
                )
            validate_stage0_setup(exp, case_id, setup_dir, extra_pythonpath=extra_pp)
            return setup_dir, digest_file(receipt_path)
        # Input pins changed without invalidation: run setup in place so commands
        # like reference_metric.py can refuse with "metric-setup changed; use a new work root".
        atomic_write_bytes(setup_dir / "request.json", req_bytes)
    else:
        if setup_dir.exists():
            unexpected = [p.name for p in setup_dir.iterdir() if p.name != "request.json"]
            if unexpected:
                raise ValueError(
                    f"case {case_id}, path {setup_dir}: incomplete metric-setup directory; use --invalidate-stage setup"
                )
        setup_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(setup_dir / "request.json", req_bytes)

    cmd = [*setup_cfg.command, "--request", "request.json", "--output-dir", "."]
    try:
        returncode, timed_out, stdout, stderr = run_command_subprocess(
            cmd,
            cwd=setup_dir,
            timeout_seconds=setup_cfg.timeout_seconds,
            extra_pythonpath=extra_pp,
            kill_grace_seconds=kill_grace_seconds,
        )
    except OSError as exc:
        raise ValueError(f"case {case_id}, path {setup_dir}: failed to launch metric.setup.command: {exc}") from exc

    if timed_out:
        raise ValueError(
            f"case {case_id}, path {setup_dir}: metric.setup.command timed out after {setup_cfg.timeout_seconds}s"
        )
    if returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
        raise ValueError(
            f"case {case_id}, path {setup_dir}: metric.setup.command failed ({returncode}): {detail or 'no output'}"
        )

    validate_stage0_setup(exp, case_id, setup_dir, extra_pythonpath=extra_pp)
    receipt = Stage0SetupReceipt(
        case_id=case_id,
        command=setup_cfg.command,
        setup_identity_sha256=computed.setup_identity_sha256,
        setup_digest_sha256=computed.setup_digest_sha256,
        output_module_sha256=computed.setup_output_module_sha256,
        case_lock_sha256=case_lock_sha256,
        request_sha256=request_sha256,
        files=_dir_manifest_excluding(setup_dir, {"request.json", "receipt.json"}),
    )
    atomic_write_bytes(receipt_path, json_bytes(receipt.to_payload()))
    return setup_dir, digest_file(receipt_path)


def _stage1_job_config(
    *,
    loaded: LoadedExperiment,
    lc: LoadedCase,
    execution_dir: Path,
    sorted_tasks: list[Task],
) -> JobConfig:
    exp = loaded.experiment
    if set(exp.execution.environment) - EnvironmentConfig.model_fields.keys():
        raise ValueError("unknown Harbor environment setting")
    agent_cfg = resolve_harbor_agent_config(lc.agent, lc.cases_root)
    task_paths = [lc.task_paths[t.task_id] for t in sorted_tasks]
    return JobConfig.model_validate(
        {
            "job_name": "run-0001",
            "jobs_dir": str(execution_dir / "agents" / "agent"),
            "n_attempts": 1,
            "retry": {"max_retries": 0, "exclude_exceptions": []},
            "n_concurrent_trials": exp.execution.n_concurrent_trials,
            "environment": exp.execution.environment,
            "agents": [agent_cfg.model_dump(mode="json")],
            "tasks": [{"path": str(p)} for p in task_paths],
            "quiet": True,
        }
    )


def _run_stage1_execution(
    *,
    loaded: LoadedExperiment,
    verified: dict[str, LockEntry],
    lc: LoadedCase,
    case_dir: Path,
    runner: Callable[[JobConfig], None],
) -> tuple[Path, str, Stage1Results]:
    exp = loaded.experiment
    case_id = lc.definition.case_id
    installed_harbor = version("harbor")
    if installed_harbor != exp.execution.harbor_version:
        raise ValueError(
            f"case {case_id}: configured execution.harbor_version is {exp.execution.harbor_version!r}, "
            f"but installed harbor is {installed_harbor!r}"
        )

    execution_dir = case_dir / "execution"
    sorted_tasks = sorted(lc.tasks.task_inventory.tasks, key=lambda t: t.key())

    job_request_payload = {
        "execution_sha256": verified["execution"].sha256,
        "case_id": case_id,
        "agent_bundle_id": lc.agent.agent_bundle_id,
        "agent_sha256": verified[f"cases[{case_id}].agent"].sha256,
        "agent_inputs_sha256": {
            name: verified[f"cases[{case_id}].agent.inputs.{name}"].sha256 for name in sorted(lc.agent_input_paths)
        },
        "agent_digest_sha256": (
            verified[f"cases[{case_id}].agent.digest"].sha256 if lc.agent.digest is not None else None
        ),
        "tasks_sha256": {t.task_id: verified[f"cases[{case_id}].tasks.{t.task_id}"].sha256 for t in sorted_tasks},
    }
    job_request_bytes = json_bytes(job_request_payload)
    job_request_sha256 = digest_bytes(job_request_bytes)

    jobs_dir = execution_dir / "agents" / "agent"
    job_dir = jobs_dir / "run-0001"
    job_receipt_path = jobs_dir / "receipt.json"
    exec_receipt_path = execution_dir / "receipt.json"
    results_path = execution_dir / "results.json"

    if job_receipt_path.is_file() and exec_receipt_path.is_file() and results_path.is_file():
        job_receipt = read_json(job_receipt_path)
        exec_receipt = Stage1ExecutionReceipt.model_validate(read_json(exec_receipt_path))
        if not isinstance(job_receipt, dict):
            raise ValueError(f"case {case_id}, path {job_receipt_path}: invalid job receipt")
        native_req_path = jobs_dir / "native-request.json"
        if not native_req_path.is_file() or not job_dir.is_dir():
            raise ValueError(
                f"case {case_id}, path {execution_dir}: incomplete published execution directory; "
                "use --invalidate-stage baseline"
            )
        current_job_files = directory_manifest(job_dir)
        saved_job_files = job_receipt.get("files")
        if (
            current_job_files != saved_job_files
            or digest_file(native_req_path) != job_receipt.get("native_request_sha256")
            or digest_file(results_path) != exec_receipt.results_sha256
            or digest_file(job_receipt_path) != exec_receipt.job_receipt_sha256
        ):
            raise ValueError(
                f"case {case_id}, path {execution_dir}: published execution directory changed; "
                "use --invalidate-stage baseline"
            )
        if (
            job_receipt.get("configuration_sha256") == job_request_sha256
            and exec_receipt.request_sha256 == job_request_sha256
        ):
            results = validate_stage1_execution(execution_dir, lc)
            return execution_dir, digest_file(exec_receipt_path), results
        # Upstream pins changed without tampering: replace execution directory cleanly.
        shutil.rmtree(execution_dir)
    elif execution_dir.exists():
        # Clean up/reject partial unsealed execution directories so incomplete Harbor state isn't silently reused.
        if job_dir.exists() or job_receipt_path.exists() or exec_receipt_path.exists():
            raise ValueError(
                f"case {case_id}, path {execution_dir}: incomplete execution directory; use --invalidate-stage baseline"
            )

    job_config = _stage1_job_config(loaded=loaded, lc=lc, execution_dir=execution_dir, sorted_tasks=sorted_tasks)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    native_req_path = jobs_dir / "native-request.json"
    atomic_write_bytes(jobs_dir / "request.json", job_request_bytes)
    atomic_write_bytes(native_req_path, json_bytes(job_config.model_dump(mode="json")))

    runner(job_config)
    rows = collect_stage1_job(lc, execution_dir)
    results = Stage1Results(
        run_id="agent/run-0001",
        case_id=case_id,
        task_inventory=lc.tasks.task_inventory.tasks,
        execution_results=rows,
    )
    results_bytes = json_bytes(results.model_dump(mode="json", warnings="error"))
    atomic_write_bytes(results_path, results_bytes)
    validated_results = validate_stage1_execution(execution_dir, lc)

    job_receipt_payload = {
        "configuration_sha256": job_request_sha256,
        "native_request_sha256": digest_file(native_req_path),
        "files": directory_manifest(job_dir),
    }
    atomic_write_bytes(job_receipt_path, json_bytes(job_receipt_payload))
    atomic_write_bytes(
        execution_dir / "execution.json",
        json_bytes(
            {
                "schema": "nemo.eval_author.stage1_execution_manifest.v1",
                "case_id": case_id,
                "execution_sha256": verified["execution"].sha256,
            }
        ),
    )
    exec_receipt = Stage1ExecutionReceipt(
        case_id=case_id,
        request_sha256=job_request_sha256,
        job_receipt_sha256=digest_file(job_receipt_path),
        results_sha256=digest_file(results_path),
    )
    atomic_write_bytes(exec_receipt_path, json_bytes(exec_receipt.to_payload()))
    return execution_dir, digest_file(exec_receipt_path), validated_results


def _run_or_reuse_metric_score(
    *,
    loaded: LoadedExperiment,
    computed: ComputedLock,
    verified: dict[str, LockEntry],
    lc: LoadedCase,
    scope: Literal["full", "subset", "plus"],
    subset_id: str | None,
    inv_dir: Path,
    tasks: list[Task],
    rows: list[Stage1ExecutionRow],
    execution_dir: Path,
    execution_receipt_sha256: str,
    metric_setup_dir: Path | None,
    metric_setup_sha256: str | None,
    kill_grace_seconds: float,
) -> tuple[MetricCommandReceipt, str]:
    exp = loaded.experiment
    case_id = lc.definition.case_id
    score_cfg = exp.metric.score
    extra_pp = [loaded.experiment_dir]
    settings_sha = _case_settings_digests(case_id, lc, verified)

    sorted_rows = sorted(rows, key=lambda r: r.task.key())
    trial_entries = [
        ScoreTrialEntry(
            task_id=row.task.task_id,
            revision=row.task.revision,
            status=row.status,
            trial_path=f"workspace/trials/{row.task.task_id}",
        )
        for row in sorted_rows
    ]
    score_req = ScoreRequest(
        case_id=case_id,
        scope=scope,
        subset_id=subset_id,
        metric_setup_dir=str(metric_setup_dir) if metric_setup_dir is not None else None,
        tasks=sorted(tasks, key=lambda t: t.key()),
        trials=trial_entries,
        settings=lc.resolved_settings,
    )
    req_bytes = json_bytes(score_req.to_payload())
    request_sha256 = digest_bytes(req_bytes)
    trial_set = [row.task.task_id for row in sorted_rows]

    receipt_path = inv_dir / "receipt.json"
    output_dir = inv_dir / "output"

    if receipt_path.is_file():
        existing = MetricCommandReceipt.model_validate(read_json(receipt_path))
        if output_dir.exists():
            current_output_files = directory_manifest(output_dir)
            if current_output_files != existing.output_files:
                raise ValueError(
                    f"case {case_id}, path {inv_dir}: published metric output changed; "
                    + ("use --invalidate-stage baseline" if scope == "full" else "use --invalidate-stage subsets")
                )
        elif existing.output_files:
            raise ValueError(
                f"case {case_id}, path {inv_dir}: published metric output directory missing; "
                + ("use --invalidate-stage baseline" if scope == "full" else "use --invalidate-stage subsets")
            )

        pins_match = (
            existing.case_id == case_id
            and existing.scope == scope
            and existing.subset_id == subset_id
            and existing.command == score_cfg.command
            and existing.metric_digest == computed.score_digest_sha256
            and existing.metric_block_sha256 == verified["metric"].sha256
            and existing.metric_setup_sha256 == metric_setup_sha256
            and existing.settings_sha256 == settings_sha
            and existing.execution_receipt_sha256 == execution_receipt_sha256
            and existing.request_sha256 == request_sha256
            and existing.trial_set == trial_set
        )
        if pins_match:
            if existing.status == "complete":
                validate_directory_output(
                    score_cfg.output,
                    output_dir,
                    timeout_seconds=score_cfg.timeout_seconds,
                    require_score_output=True,
                    case_id=case_id,
                    subset_id=subset_id,
                    extra_pythonpath=extra_pp,
                    kill_grace_seconds=kill_grace_seconds,
                )
            return existing, digest_file(receipt_path)
        shutil.rmtree(inv_dir)

    inv_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = inv_dir / "workspace"
    trials_dest = workspace_dir / "trials"
    trials_dest.mkdir(parents=True, exist_ok=True)
    for row in sorted_rows:
        copy_tree_filtered(execution_dir / row.native_results_path, trials_dest / row.task.task_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(inv_dir / "request.json", req_bytes)

    result = invoke_workspace_command(
        score_cfg.command,
        invocation_dir=inv_dir,
        timeout_seconds=score_cfg.timeout_seconds,
        workspace_dir=workspace_dir,
        output_arg="output",
        extra_pythonpath=extra_pp,
        kill_grace_seconds=kill_grace_seconds,
    )

    unavailable_reason: str | None = None
    parsed_score: Score | None = None
    if result.workspace_modified:
        unavailable_reason = "workspace_modified"
        if output_dir.exists():
            shutil.rmtree(output_dir)
    elif result.timed_out:
        unavailable_reason = result.error or f"metric.score.command timed out after {score_cfg.timeout_seconds}s"
    elif not result.succeeded:
        unavailable_reason = result.error or f"metric.score.command failed ({result.returncode})"
    else:
        try:
            validated = validate_directory_output(
                score_cfg.output,
                output_dir,
                timeout_seconds=score_cfg.timeout_seconds,
                require_score_output=True,
                case_id=case_id,
                subset_id=subset_id,
                extra_pythonpath=extra_pp,
                kill_grace_seconds=kill_grace_seconds,
            )
            score_obj = validated.get("score")
            if not isinstance(score_obj, Score):
                raise ValueError(f"case {case_id}, path {output_dir}: missing validated score")
            parsed_score = score_obj
            if scope == "subset" and parsed_score.value is None:
                unavailable_reason = parsed_score.missing_score_reason or "null_score"
            elif scope == "full" and parsed_score.value is None:
                raise ValueError(
                    f"case {case_id}, path {output_dir / 'score.json'}: "
                    f"stage 1 requires numeric M(B), got null ({parsed_score.missing_score_reason})"
                )
        except ValueError as exc:
            if scope == "full":
                raise
            unavailable_reason = str(exc)

    if unavailable_reason is not None and scope == "full":
        raise ValueError(f"case {case_id}, path {inv_dir}: {unavailable_reason}")

    status: Literal["complete", "unavailable"] = "unavailable" if unavailable_reason is not None else "complete"
    output_manifest = directory_manifest(output_dir) if output_dir.is_dir() else {}
    receipt = MetricCommandReceipt(
        case_id=case_id,
        scope=scope,
        subset_id=subset_id,
        command=score_cfg.command,
        metric_digest=computed.score_digest_sha256,
        metric_block_sha256=verified["metric"].sha256,
        metric_setup_sha256=metric_setup_sha256,
        settings_sha256=settings_sha,
        execution_receipt_sha256=execution_receipt_sha256,
        request_sha256=request_sha256,
        trial_set=trial_set,
        status=status,  # type: ignore[arg-type]
        score=parsed_score,
        unavailable_reason=unavailable_reason,
        output_files=output_manifest,
    )
    atomic_write_bytes(receipt_path, json_bytes(receipt.to_payload()))
    return receipt, digest_file(receipt_path)


def _run_stage2_subsets(
    *,
    loaded: LoadedExperiment,
    computed: ComputedLock,
    verified: dict[str, LockEntry],
    lc: LoadedCase,
    case_dir: Path,
    execution_dir: Path,
    execution_receipt_sha256: str,
    results: Stage1Results,
    metric_setup_dir: Path | None,
    metric_setup_sha256: str | None,
    kill_grace_seconds: float,
) -> dict[str, MetricCommandReceipt]:
    case_id = lc.definition.case_id
    subsets_dir = case_dir / "metric-subsets"
    stage_receipt_path = subsets_dir / "receipt.json"
    subsets_lock_sha256 = verified[f"cases[{case_id}].subsets"].sha256
    settings_sha = _case_settings_digests(case_id, lc, verified)

    sorted_subsets = sorted(lc.subsets.eval_subsets, key=lambda s: s.eval_subset_id)
    subset_dirs = {sid: subsets_dir / name for sid, name in subset_dir_names(lc).items()}

    if stage_receipt_path.is_file():
        prev_stage = SubsetsStageReceipt.model_validate(read_json(stage_receipt_path))
        for subset_id, rec_sha in prev_stage.subset_receipts.items():
            sub_dir = subset_dirs.get(subset_id)
            sub_receipt_path = sub_dir / "receipt.json" if sub_dir is not None else None
            if sub_receipt_path is None or not sub_receipt_path.is_file() or digest_file(sub_receipt_path) != rec_sha:
                raise ValueError(
                    f"case {case_id}, path {subsets_dir}: published metric-subsets directory changed; "
                    "use --invalidate-stage subsets"
                )
        stage_pins_match = (
            prev_stage.case_id == case_id
            and prev_stage.subsets_lock_sha256 == subsets_lock_sha256
            and prev_stage.execution_receipt_sha256 == execution_receipt_sha256
            and prev_stage.metric_block_sha256 == verified["metric"].sha256
            and prev_stage.metric_digest == computed.score_digest_sha256
            and prev_stage.metric_setup_sha256 == metric_setup_sha256
            and prev_stage.settings_sha256 == settings_sha
            and set(prev_stage.subset_receipts.keys()) == set(subset_dirs.keys())
        )
        if stage_pins_match:
            # Verify each subset directory individually (also checks output_files tamper) and reuse.
            reused: dict[str, MetricCommandReceipt] = {}
            row_by_id = {r.task.task_id: r for r in results.execution_results}
            for subset in sorted_subsets:
                sub_rows = [row_by_id[tid] for tid in subset.task_ids]
                receipt, _ = _run_or_reuse_metric_score(
                    loaded=loaded,
                    computed=computed,
                    verified=verified,
                    lc=lc,
                    scope="subset",
                    subset_id=subset.eval_subset_id,
                    inv_dir=subset_dirs[subset.eval_subset_id],
                    tasks=[r.task for r in sub_rows],
                    rows=sub_rows,
                    execution_dir=execution_dir,
                    execution_receipt_sha256=execution_receipt_sha256,
                    metric_setup_dir=metric_setup_dir,
                    metric_setup_sha256=metric_setup_sha256,
                    kill_grace_seconds=kill_grace_seconds,
                )
                reused[subset.eval_subset_id] = receipt
            return reused
        shutil.rmtree(subsets_dir)

    subsets_dir.mkdir(parents=True, exist_ok=True)
    row_by_id = {r.task.task_id: r for r in results.execution_results}
    subset_results: dict[str, MetricCommandReceipt] = {}
    subset_receipt_digests: dict[str, str] = {}
    for subset in sorted_subsets:
        sub_rows = [row_by_id[tid] for tid in subset.task_ids]
        receipt, rec_sha = _run_or_reuse_metric_score(
            loaded=loaded,
            computed=computed,
            verified=verified,
            lc=lc,
            scope="subset",
            subset_id=subset.eval_subset_id,
            inv_dir=subset_dirs[subset.eval_subset_id],
            tasks=[r.task for r in sub_rows],
            rows=sub_rows,
            execution_dir=execution_dir,
            execution_receipt_sha256=execution_receipt_sha256,
            metric_setup_dir=metric_setup_dir,
            metric_setup_sha256=metric_setup_sha256,
            kill_grace_seconds=kill_grace_seconds,
        )
        subset_results[subset.eval_subset_id] = receipt
        subset_receipt_digests[subset.eval_subset_id] = rec_sha

    stage_receipt = SubsetsStageReceipt(
        case_id=case_id,
        subsets_lock_sha256=subsets_lock_sha256,
        execution_receipt_sha256=execution_receipt_sha256,
        metric_block_sha256=verified["metric"].sha256,
        metric_digest=computed.score_digest_sha256,
        metric_setup_sha256=metric_setup_sha256,
        settings_sha256=settings_sha,
        subset_receipts=subset_receipt_digests,
    )
    atomic_write_bytes(stage_receipt_path, json_bytes(stage_receipt.to_payload()))
    return subset_results


def run_experiment(
    benchmark: Path,
    run_id: str,
    work_root: Path,
    *,
    invalidate_stages: list[str] | None = None,
    case_filter: str | None = None,
    runner: Callable[[JobConfig], None] = run_harbor,
    kill_grace_seconds: float = 60.0,
    max_gaps: int | None = None,
) -> dict[str, Any]:
    """Run stages 0-3 on a signed generation experiment."""
    if not run_id.strip():
        raise ValueError("run_id must be nonempty")
    if max_gaps is not None and max_gaps < 1:
        raise ValueError("--max-gaps must be at least 1")

    stages_to_invalidate = list(invalidate_stages or [])
    unknown_stages = [s for s in stages_to_invalidate if s not in INVALIDATE_STAGE_CHOICES]
    if unknown_stages:
        raise ValueError(
            f"unknown --invalidate-stage value(s): {', '.join(unknown_stages)}; "
            f"allowed: {', '.join(INVALIDATE_STAGE_CHOICES)}"
        )

    computed, actual_lock = check_experiment(benchmark)
    loaded = computed.loaded
    exp = loaded.experiment

    if case_filter is not None and case_filter not in loaded.cases:
        available = ", ".join(loaded.cases.keys())
        raise ValueError(f"unknown --case {case_filter!r}; available cases: {available}")

    target_cases = [loaded.cases[case_filter]] if case_filter is not None else list(loaded.cases.values())

    # Stage 0-2 receipts record only these entries; stage 3 adds the generation entries below.
    read_keys: list[str] = ["cases", "execution", "metric"]
    if exp.metric.setup is not None:
        read_keys.append("metric.setup.output.type")
        if exp.metric.setup.digest is not None:
            read_keys.append("metric.setup.digest")
    if exp.metric.score.digest is not None:
        read_keys.append("metric.score.digest")
    if exp.metric.score.output is not None:
        read_keys.append("metric.score.output.type")

    for lc in target_cases:
        cid = lc.definition.case_id
        read_keys.extend(
            [
                f"cases[{cid}].agent",
                f"cases[{cid}].tasks",
                f"cases[{cid}].subsets",
                f"cases[{cid}].ethos",
                *(f"cases[{cid}].agent.inputs.{k}" for k in lc.agent_input_paths),
                *(f"cases[{cid}].tasks.{t}" for t in lc.task_paths),
                *(f"cases[{cid}].settings.{k}" for k in lc.case_settings_refs),
            ]
        )
        if lc.agent.digest is not None:
            read_keys.append(f"cases[{cid}].agent.digest")

    verified = verify_lock_entries(actual_lock, computed.lock, read_keys)
    generation_verified = {
        **verified,
        **verify_lock_entries(actual_lock, computed.lock, generation_lock_keys(loaded)),
    }

    work_root = work_root.resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    _update_generation_inputs(
        work_root / "generation-inputs.json",
        run_id=run_id,
        used_entries=generation_verified,
    )
    implementation_sha256 = implementation_digest()

    for stage in stages_to_invalidate:
        for lc in target_cases:
            case_dir = work_root / "cases" / f"case-{lc.index:04d}"
            _invalidate_case_stages(case_dir, lc.definition.case_id, stage)

    summary_cases: dict[str, Any] = {}
    for lc in target_cases:
        cid = lc.definition.case_id
        case_dir = work_root / "cases" / f"case-{lc.index:04d}"

        metric_setup_dir, metric_setup_sha256 = _run_stage0_setup(
            loaded=loaded,
            computed=computed,
            verified=verified,
            lc=lc,
            case_dir=case_dir,
            kill_grace_seconds=kill_grace_seconds,
        )

        execution_dir, exec_receipt_sha256, results = _run_stage1_execution(
            loaded=loaded,
            verified=verified,
            lc=lc,
            case_dir=case_dir,
            runner=runner,
        )

        full_receipt, _ = _run_or_reuse_metric_score(
            loaded=loaded,
            computed=computed,
            verified=verified,
            lc=lc,
            scope="full",
            subset_id=None,
            inv_dir=case_dir / "metric-full",
            tasks=results.task_inventory,
            rows=results.execution_results,
            execution_dir=execution_dir,
            execution_receipt_sha256=exec_receipt_sha256,
            metric_setup_dir=metric_setup_dir,
            metric_setup_sha256=metric_setup_sha256,
            kill_grace_seconds=kill_grace_seconds,
        )

        subset_receipts = _run_stage2_subsets(
            loaded=loaded,
            computed=computed,
            verified=verified,
            lc=lc,
            case_dir=case_dir,
            execution_dir=execution_dir,
            execution_receipt_sha256=exec_receipt_sha256,
            results=results,
            metric_setup_dir=metric_setup_dir,
            metric_setup_sha256=metric_setup_sha256,
            kill_grace_seconds=kill_grace_seconds,
        )

        generation_receipts = generate(
            loaded,
            generation_verified,
            lc,
            case_dir,
            max_gaps=max_gaps,
            kill_grace_seconds=kill_grace_seconds,
            implementation_sha256=implementation_sha256,
        )

        summary_cases[cid] = {
            "case_dir": str(case_dir),
            "metric_setup_sha256": metric_setup_sha256,
            "m_full": full_receipt.score.model_dump(mode="json") if full_receipt.score else None,
            "subsets": {
                sid: {
                    "status": rec.status,
                    "score": rec.score.model_dump(mode="json") if rec.score else None,
                    "unavailable_reason": rec.unavailable_reason,
                }
                for sid, rec in subset_receipts.items()
            },
            "generation": {
                sid: {
                    "status": rec.status,
                    "generated_tasks": rec.generated_tasks,
                    "unavailable_reason": rec.unavailable_reason,
                }
                for sid, rec in generation_receipts.items()
            },
        }

    return {
        "run_id": run_id,
        "work_root": str(work_root),
        "cases": summary_cases,
    }
