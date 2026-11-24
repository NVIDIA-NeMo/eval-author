# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage 3: stage each subset's workspace, run `generation.command`, and publish its receipt."""

from __future__ import annotations

import copy
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath

from metric_benchmark.io import digest_bytes, digest_file, json_bytes, read_json

from generation_benchmark.digests import directory_manifest, implementation_digest, is_skipped_path
from generation_benchmark.models import (
    GenerationAgentRef,
    GenerationRequest,
    GenerationResponse,
    LoadedCase,
    LoadedExperiment,
    LockEntry,
    MetricCommandReceipt,
    Stage3GenerationReceipt,
    Subset,
    SubsetsStageReceipt,
    relative_path,
    resolve_settings_paths,
)
from generation_benchmark.validation import (
    check_generation_output_tree,
    invoke_workspace_command,
    validate_directory_output,
    validate_generation_output,
)

_STDERR_TAIL_BYTES = 4000


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def subset_dir_names(lc: LoadedCase) -> dict[str, str]:
    """Map each subset id to its `subset-NNNNNN` directory name, in sorted subset id order."""
    ordered = sorted(s.eval_subset_id for s in lc.subsets.eval_subsets)
    return {subset_id: f"subset-{idx:06d}" for idx, subset_id in enumerate(ordered)}


def _exclude_prefixes(prefixes: list[str]) -> tuple[PurePosixPath, ...]:
    normalized: list[PurePosixPath] = []
    for raw in prefixes:
        value = raw.rstrip("/")
        try:
            relative_path(value)
        except ValueError:
            raise ValueError(f"agent_source_excludes entry must be a relative path prefix: {raw!r}") from None
        normalized.append(PurePosixPath(value))
    return tuple(normalized)


def _is_excluded(rel: PurePosixPath, prefixes: tuple[PurePosixPath, ...]) -> bool:
    return any(rel.parts[: len(prefix.parts)] == prefix.parts for prefix in prefixes)


def copy_tree_filtered(src: Path, dst: Path, *, exclude_prefixes: tuple[PurePosixPath, ...] = ()) -> None:
    """Copy *src* to *dst*, skipping the skipped names and *exclude_prefixes*, and rejecting symlinks."""
    src = src.resolve()
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src, followlinks=False):
        root_path = Path(root)
        rel_root = PurePosixPath(root_path.relative_to(src).as_posix())
        pruned: list[str] = []
        for dirname in sorted(dirs):
            rel = rel_root / dirname
            if is_skipped_path(rel.parts) or _is_excluded(rel, exclude_prefixes):
                continue
            full_dir = root_path / dirname
            if full_dir.is_symlink():
                raise ValueError(f"symlinks are not allowed in staged tree: {full_dir}")
            (dst / rel).mkdir(parents=True, exist_ok=True)
            pruned.append(dirname)
        dirs[:] = pruned
        for filename in sorted(files):
            rel = rel_root / filename
            if is_skipped_path(rel.parts) or _is_excluded(rel, exclude_prefixes):
                continue
            full_file = root_path / filename
            if full_file.is_symlink() or not full_file.is_file():
                raise ValueError(f"non-regular file not allowed in staged tree: {full_file}")
            shutil.copy2(full_file, dst / rel)


def generation_lock_keys(loaded: LoadedExperiment) -> list[str]:
    """Lock entries stage 3 reads: `generation`, its `digest`, and its settings references."""
    keys = ["generation"]
    if loaded.experiment.generation.digest is not None:
        keys.append("generation.digest")
    keys.extend(f"generation.settings.{dotted}" for dotted in sorted(loaded.generation_settings_refs))
    return keys


def generation_configuration_sha256(
    verified: dict[str, LockEntry],
    keys: list[str],
    *,
    implementation_sha256: str,
    max_gaps: int | None,
) -> str:
    payload = {
        "lock": {key: verified[key].sha256 for key in sorted(keys)},
        "implementation_sha256": implementation_sha256,
        "max_gaps": max_gaps,
    }
    return digest_bytes(json_bytes(payload))


def _case_lock_sha256(lc: LoadedCase, subset: Subset, verified: dict[str, LockEntry]) -> dict[str, str]:
    cid = lc.definition.case_id
    keys = [
        f"cases[{cid}].agent",
        f"cases[{cid}].ethos",
        f"cases[{cid}].subsets",
        f"cases[{cid}].tasks",
        *(f"cases[{cid}].agent.inputs.{name}" for name in sorted(lc.agent_input_paths)),
        *(f"cases[{cid}].tasks.{tid}" for tid in sorted(subset.task_ids)),
    ]
    if lc.agent.digest is not None:
        keys.append(f"cases[{cid}].agent.digest")
    pins = {key: verified[key].sha256 for key in keys}
    pins[f"cases[{cid}].agent_source_excludes"] = digest_bytes(json_bytes(lc.definition.agent_source_excludes))
    return pins


def held_out_task_ids(lc: LoadedCase, subset: Subset) -> list[str]:
    in_subset = set(subset.task_ids)
    return sorted(tid for tid in lc.task_paths if tid not in in_subset)


def stage_workspace(lc: LoadedCase, subset: Subset, workspace_dir: Path, *, work_root: Path | None = None) -> None:
    """Write `ETHOS.md`, `agent/` (after `agent_source_excludes`), and Bᵢ's `tasks/<task_id>/` only."""
    excludes = _exclude_prefixes(lc.definition.agent_source_excludes)
    agent_root = lc.agent_path.parent.resolve()
    if work_root is not None and work_root.resolve().is_relative_to(agent_root):
        raise ValueError(f"work root {work_root} is inside the agent source tree {agent_root}")
    for tid in held_out_task_ids(lc, subset):
        task_path = lc.task_paths[tid].resolve()
        if agent_root.is_relative_to(task_path):
            raise ValueError(f"agent source tree {agent_root} is inside held-out task {tid}")
        if task_path.is_relative_to(agent_root):
            rel = PurePosixPath(task_path.relative_to(agent_root).as_posix())
            if not _is_excluded(rel, excludes) and not is_skipped_path(rel.parts):
                raise ValueError(
                    f"held-out task {tid} at {task_path} is inside the agent source tree; "
                    "add its prefix to agent_source_excludes"
                )

    workspace_dir.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(lc.ethos_path, workspace_dir / "ETHOS.md")
    copy_tree_filtered(agent_root, workspace_dir / "agent", exclude_prefixes=excludes)
    tasks_dir = workspace_dir / "tasks"
    tasks_dir.mkdir()
    for tid in subset.task_ids:
        copy_tree_filtered(lc.task_paths[tid], tasks_dir / tid)


def build_generation_request(
    loaded: LoadedExperiment,
    lc: LoadedCase,
    subset: Subset,
    *,
    metric_output_dir: Path,
    max_gaps: int | None,
) -> GenerationRequest:
    in_subset = set(subset.task_ids)
    tasks = sorted((t for t in lc.tasks.task_inventory.tasks if t.task_id in in_subset), key=lambda t: t.key())
    return GenerationRequest(
        case_id=lc.definition.case_id,
        eval_subset_id=subset.eval_subset_id,
        tasks=tasks,
        metric_output_dir=str(metric_output_dir),
        agent=GenerationAgentRef(
            agent_bundle_id=lc.agent.agent_bundle_id,
            revision=lc.agent.revision,
            harbor=copy.deepcopy(lc.agent.harbor),
        ),
        settings=resolve_settings_paths(loaded.experiment.generation.settings, loaded.experiment_dir),
        max_gaps=max_gaps,
    )


def _generated_tasks(output_dir: Path) -> list[str]:
    tasks_dir = output_dir / "tasks"
    if not tasks_dir.is_dir():
        return []
    return sorted(entry.name for entry in tasks_dir.iterdir() if entry.is_dir() and not entry.is_symlink())


def _attempt_summary(response: GenerationResponse | None) -> dict[str, object]:
    if response is None:
        return {"response": "missing_or_invalid"}
    return {
        "response": "valid",
        "attempts": len(response.attempts),
        "by_status": dict(sorted(Counter(attempt.status for attempt in response.attempts).items())),
        "unattempted": list(response.unattempted),
        "error": response.error,
    }


def _stderr_tail(stderr: bytes) -> str:
    return stderr[-_STDERR_TAIL_BYTES:].decode("utf-8", errors="replace")


def _load_upstream_receipts(
    lc: LoadedCase, case_dir: Path
) -> tuple[MetricCommandReceipt, str, dict[str, tuple[MetricCommandReceipt, str]]]:
    cid = lc.definition.case_id
    full_path = case_dir / "metric-full" / "receipt.json"
    stage2_path = case_dir / "metric-subsets" / "receipt.json"
    for path in (full_path, stage2_path):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"case {cid}, path {path.parent}: missing stage receipt; run stages 0-2 first")
    full = MetricCommandReceipt.model_validate(read_json(full_path))
    stage2 = SubsetsStageReceipt.model_validate(read_json(stage2_path))
    subsets: dict[str, tuple[MetricCommandReceipt, str]] = {}
    for subset_id, name in subset_dir_names(lc).items():
        path = case_dir / "metric-subsets" / name / "receipt.json"
        if path.is_symlink() or not path.is_file() or stage2.subset_receipts.get(subset_id) != digest_file(path):
            raise ValueError(
                f"case {cid}, path {path.parent}: published metric-subsets directory changed; "
                "use --invalidate-stage subsets"
            )
        subsets[subset_id] = (MetricCommandReceipt.model_validate(read_json(path)), digest_file(path))
    return full, digest_file(full_path), subsets


def _check_published(published: Path, existing: Stage3GenerationReceipt, *, case_id: str) -> None:
    output_dir = published / "output"
    try:
        current = directory_manifest(output_dir) if output_dir.exists() or output_dir.is_symlink() else {}
        request_ok = (
            existing.request_sha256 is None or digest_file(published / "request.json") == existing.request_sha256
        )
    except (OSError, ValueError):
        current, request_ok = None, False
    if current != existing.output_files or not request_ok:
        raise ValueError(
            f"case {case_id}, path {published}: published generation directory changed; use --invalidate-stage generate"
        )


def _run_subset(
    *,
    loaded: LoadedExperiment,
    lc: LoadedCase,
    subset: Subset,
    case_dir: Path,
    published: Path,
    full: MetricCommandReceipt,
    full_sha: str,
    sub: MetricCommandReceipt,
    sub_sha: str,
    metric_output_dir: Path,
    configuration_sha256: str,
    case_lock_sha256: dict[str, str],
    max_gaps: int | None,
    kill_grace_seconds: float,
) -> Stage3GenerationReceipt:
    exp = loaded.experiment
    cid = lc.definition.case_id
    sid = subset.eval_subset_id

    precheck: Stage3GenerationReceipt | None = None
    base: dict[str, object] = {
        "case_id": cid,
        "eval_subset_id": sid,
        "command": exp.generation.command,
        "configuration_sha256": configuration_sha256,
        "metric_full_receipt_sha256": full_sha,
        "metric_subset_receipt_sha256": sub_sha,
        "case_lock_sha256": case_lock_sha256,
    }
    m_full = full.score.value if full.score is not None else None
    m_sub = sub.score.value if sub.score is not None else None
    if sub.status != "complete" or m_sub is None or m_full is None:
        reason = sub.unavailable_reason or (sub.score.missing_score_reason if sub.score is not None else None)
        precheck = Stage3GenerationReceipt.model_validate(
            {
                **base,
                "status": "unavailable",
                "unavailable_reason": f"stage 2 M(Bᵢ) unavailable: {reason or 'null score'}",
            }
        )
    elif m_full == m_sub:
        precheck = Stage3GenerationReceipt.model_validate({**base, "status": "zero_gap"})

    request = build_generation_request(loaded, lc, subset, metric_output_dir=metric_output_dir, max_gaps=max_gaps)
    request_bytes = json_bytes(request.to_payload())
    request_sha256 = None if precheck is not None else digest_bytes(request_bytes)

    if published.is_symlink():
        raise ValueError(f"case {cid}, path {published}: published generation directory must not be a symlink")
    if published.exists():
        receipt_path = published / "receipt.json"
        try:
            existing = Stage3GenerationReceipt.model_validate(read_json(receipt_path))
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"case {cid}, path {published}: incomplete published generation directory ({exc}); "
                "use --invalidate-stage generate"
            ) from exc
        pins_match = (
            existing.case_id == cid
            and existing.eval_subset_id == sid
            and existing.command == exp.generation.command
            and existing.configuration_sha256 == configuration_sha256
            and existing.request_sha256 == request_sha256
            and existing.metric_full_receipt_sha256 == full_sha
            and existing.metric_subset_receipt_sha256 == sub_sha
            and existing.case_lock_sha256 == case_lock_sha256
        )
        if pins_match:
            _check_published(published, existing, case_id=cid)
            return existing
        shutil.rmtree(published)

    gen_root = published.parent
    gen_root.mkdir(parents=True, exist_ok=True)
    for stale in gen_root.glob(f"{published.name}.pending-*"):
        if stale.is_dir() and not stale.is_symlink():
            shutil.rmtree(stale)
        else:
            stale.unlink()
    pending = gen_root / f"{published.name}.pending-{os.getpid()}"
    pending.mkdir()
    try:
        receipt = precheck or _stage_and_run(
            loaded=loaded,
            lc=lc,
            subset=subset,
            case_dir=case_dir,
            pending=pending,
            sub=sub,
            metric_output_dir=metric_output_dir,
            request_bytes=request_bytes,
            base={**base, "request_sha256": request_sha256},
            kill_grace_seconds=kill_grace_seconds,
        )
        atomic_write_bytes(pending / "receipt.json", json_bytes(receipt.to_payload()))
        pending.rename(published)
    except BaseException:
        shutil.rmtree(pending, ignore_errors=True)
        raise
    return receipt


def _stage_and_run(
    *,
    loaded: LoadedExperiment,
    lc: LoadedCase,
    subset: Subset,
    case_dir: Path,
    pending: Path,
    sub: MetricCommandReceipt,
    metric_output_dir: Path,
    request_bytes: bytes,
    base: dict[str, object],
    kill_grace_seconds: float,
) -> Stage3GenerationReceipt:
    exp = loaded.experiment
    cid = lc.definition.case_id
    sid = subset.eval_subset_id
    extra_pp = [loaded.experiment_dir]

    def unavailable(reason: str, **extra: object) -> Stage3GenerationReceipt:
        return Stage3GenerationReceipt.model_validate(
            {**base, **extra, "status": "unavailable", "unavailable_reason": reason}
        )

    atomic_write_bytes(pending / "request.json", request_bytes)
    workspace_dir = pending / "workspace"
    try:
        stage_workspace(lc, subset, workspace_dir, work_root=case_dir.parent.parent)
    except (OSError, ValueError) as exc:
        return unavailable(f"case {cid}, subset {sid}, path {workspace_dir}: staging failed: {exc}")

    try:
        current_metric_files = directory_manifest(metric_output_dir)
    except (OSError, ValueError):
        current_metric_files = None
    if current_metric_files != sub.output_files:
        raise ValueError(
            f"case {cid}, path {metric_output_dir.parent}: published metric output changed; "
            "use --invalidate-stage subsets"
        )
    try:
        validate_directory_output(
            exp.metric.score.output,
            metric_output_dir,
            timeout_seconds=exp.metric.score.timeout_seconds,
            require_score_output=True,
            case_id=cid,
            subset_id=sid,
            extra_pythonpath=extra_pp,
            kill_grace_seconds=kill_grace_seconds,
        )
    except ValueError as exc:
        return unavailable(str(exc))

    output_dir = pending / "output"
    output_dir.mkdir()
    result = invoke_workspace_command(
        exp.generation.command,
        invocation_dir=pending,
        timeout_seconds=exp.generation.timeout_seconds,
        workspace_dir=workspace_dir,
        output_arg="output",
        extra_pythonpath=extra_pp,
        kill_grace_seconds=kill_grace_seconds,
    )
    failure_summary: dict[str, object] = {} if result.succeeded else {"stderr_tail": _stderr_tail(result.stderr)}
    run_fields: dict[str, object] = {
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "attempt_summary": failure_summary,
    }

    if result.workspace_modified:
        shutil.rmtree(output_dir, ignore_errors=True)
        return unavailable("workspace_modified", **run_fields)
    if not result.succeeded and not result.timed_out and result.returncode is None:
        shutil.rmtree(output_dir, ignore_errors=True)
        return unavailable(result.error or "launch failed", **run_fields)

    response: GenerationResponse | None = None
    response_error: str | None = None
    try:
        response = validate_generation_output(output_dir, case_id=cid, subset_id=sid)
    except ValueError as exc:
        response_error = str(exc)
    try:
        check_generation_output_tree(output_dir, case_id=cid, subset_id=sid)
    except ValueError as exc:
        shutil.rmtree(output_dir, ignore_errors=True)
        return unavailable(response_error or str(exc), **run_fields)

    fields = {
        **run_fields,
        "generated_tasks": _generated_tasks(output_dir),
        "attempt_summary": {**_attempt_summary(response), **failure_summary},
        "output_files": directory_manifest(output_dir),
    }

    if result.timed_out:
        return Stage3GenerationReceipt.model_validate({**base, **fields, "status": "partial"})
    if response is None:
        return unavailable(response_error or "missing or invalid response.json", **fields)
    if response.error is not None:
        return unavailable(response.error, **fields)
    if not result.succeeded:
        return Stage3GenerationReceipt.model_validate({**base, **fields, "status": "partial"})
    return Stage3GenerationReceipt.model_validate({**base, **fields, "status": "complete"})


def generate(
    loaded: LoadedExperiment,
    verified: dict[str, LockEntry],
    lc: LoadedCase,
    case_dir: Path,
    *,
    max_gaps: int | None = None,
    kill_grace_seconds: float = 60.0,
    implementation_sha256: str | None = None,
) -> dict[str, Stage3GenerationReceipt]:
    """Run stage 3 for one case, one subset at a time, in `subset-NNNNNN` order."""
    if max_gaps is not None and max_gaps < 1:
        raise ValueError("--max-gaps must be at least 1")
    full, full_sha, upstream = _load_upstream_receipts(lc, case_dir)
    configuration_sha256 = generation_configuration_sha256(
        verified,
        generation_lock_keys(loaded),
        implementation_sha256=implementation_sha256 or implementation_digest(),
        max_gaps=max_gaps,
    )
    subsets = {s.eval_subset_id: s for s in lc.subsets.eval_subsets}
    receipts: dict[str, Stage3GenerationReceipt] = {}
    for subset_id, name in subset_dir_names(lc).items():
        sub, sub_sha = upstream[subset_id]
        receipts[subset_id] = _run_subset(
            loaded=loaded,
            lc=lc,
            subset=subsets[subset_id],
            case_dir=case_dir,
            published=case_dir / "generation" / name,
            full=full,
            full_sha=full_sha,
            sub=sub,
            sub_sha=sub_sha,
            metric_output_dir=(case_dir / "metric-subsets" / name / "output").resolve(),
            configuration_sha256=configuration_sha256,
            case_lock_sha256=_case_lock_sha256(lc, subsets[subset_id], verified),
            max_gaps=max_gaps,
            kill_grace_seconds=kill_grace_seconds,
        )
    return receipts
