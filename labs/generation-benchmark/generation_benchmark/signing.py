# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Write and verify generation-experiment.lock.json without editing YAML manifests."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from metric_benchmark.io import digest_file, json_bytes, read_json

from generation_benchmark.digests import command_digest, directory_digest, glob_digest, path_digest
from generation_benchmark.models import (
    DigestSpec,
    GenerationLock,
    LoadedExperiment,
    LockEntry,
    block_sha256,
    execution_identity_form,
    generation_identity_form,
    load_experiment,
    metric_identity_form,
    metric_setup_identity_form,
)
from generation_benchmark.validation import resolve_output_type_module

LOCK_FILENAME = "generation-experiment.lock.json"


@dataclass(frozen=True)
class ComputedLock:
    loaded: LoadedExperiment
    lock: GenerationLock
    setup_identity_sha256: str | None
    setup_digest_sha256: str | None
    setup_output_module_sha256: str | None
    score_digest_sha256: str | None
    score_output_module_sha256: str | None


def lock_path_for(experiment_path: Path) -> Path:
    return experiment_path.resolve().parent / LOCK_FILENAME


def _check_executable_present(executable: str, label: str) -> None:
    candidate = Path(executable)
    if candidate.is_absolute():
        if not candidate.is_file():
            raise ValueError(f"missing {label} executable: {executable}")
        return
    if "/" in executable:
        raise ValueError(f"{label} must be an absolute path or command on PATH: {executable}")
    if shutil.which(executable) is None:
        raise ValueError(f"{label} command not found on PATH: {executable}")


def _eval_digest(spec: DigestSpec, base_dir: Path) -> str:
    if spec.method == "glob":
        return glob_digest(spec.patterns, spec.excludes, base_dir)
    return command_digest(spec.command, base_dir)


def _rel_to_experiment(target: Path, experiment_dir: Path) -> str:
    return target.resolve().relative_to(experiment_dir.resolve()).as_posix()


def _rel_module(module_file: Path, experiment_dir: Path) -> str:
    return Path(os.path.relpath(module_file.resolve(), experiment_dir.resolve())).as_posix()


def compute_lock(
    experiment_path: Path,
    *,
    extra_pythonpath: list[Path] | None = None,
) -> ComputedLock:
    """Validate experiment files, commands, output types, and digests, then build lock entries."""
    loaded = load_experiment(experiment_path)
    exp = loaded.experiment
    exp_dir = loaded.experiment_dir
    exp_name = loaded.experiment_path.name
    extra_pp = [exp_dir, *(extra_pythonpath or [])]

    if exp.metric.setup is not None:
        _check_executable_present(exp.metric.setup.command[0], "metric.setup.command")
        _check_executable_present(exp.metric.setup.output.python, "metric.setup.output.python")
    _check_executable_present(exp.metric.score.command[0], "metric.score.command")
    if exp.metric.score.output is not None:
        _check_executable_present(exp.metric.score.output.python, "metric.score.output.python")
    _check_executable_present(exp.generation.command[0], "generation.command")

    entries: dict[str, LockEntry] = {}

    entries["cases"] = LockEntry(
        type="file",
        path=_rel_to_experiment(loaded.cases_path, exp_dir),
        sha256=digest_file(loaded.cases_path),
    )

    for case_id, lc in loaded.cases.items():
        entries[f"cases[{case_id}].agent"] = LockEntry(
            type="file",
            path=_rel_to_experiment(lc.agent_path, exp_dir),
            sha256=digest_file(lc.agent_path),
        )
        for input_name, input_path in lc.agent_input_paths.items():
            entries[f"cases[{case_id}].agent.inputs.{input_name}"] = LockEntry(
                type="directory" if input_path.is_dir() else "file",
                path=_rel_to_experiment(input_path, exp_dir),
                sha256=path_digest(input_path),
            )
        if lc.agent.digest is not None:
            entries[f"cases[{case_id}].agent.digest"] = LockEntry(
                type="digest",
                path=_rel_to_experiment(lc.agent_path, exp_dir),
                selector="digest",
                sha256=_eval_digest(lc.agent.digest, lc.agent_path.parent),
            )
        entries[f"cases[{case_id}].ethos"] = LockEntry(
            type="file",
            path=_rel_to_experiment(lc.ethos_path, exp_dir),
            sha256=digest_file(lc.ethos_path),
        )
        entries[f"cases[{case_id}].subsets"] = LockEntry(
            type="file",
            path=_rel_to_experiment(lc.subsets_path, exp_dir),
            sha256=digest_file(lc.subsets_path),
        )
        entries[f"cases[{case_id}].tasks"] = LockEntry(
            type="file",
            path=_rel_to_experiment(lc.tasks_path, exp_dir),
            sha256=digest_file(lc.tasks_path),
        )
        for task_id, task_path in lc.task_paths.items():
            entries[f"cases[{case_id}].tasks.{task_id}"] = LockEntry(
                type="directory",
                path=_rel_to_experiment(task_path, exp_dir),
                sha256=directory_digest(task_path),
            )
        for dotted, (_, ref_path) in lc.case_settings_refs.items():
            entries[f"cases[{case_id}].settings.{dotted}"] = LockEntry(
                type="directory" if ref_path.is_dir() else "file",
                path=_rel_to_experiment(ref_path, exp_dir),
                sha256=path_digest(ref_path),
            )

    setup_digest_sha: str | None = None
    setup_module_sha: str | None = None
    setup_identity_sha: str | None = None
    if exp.metric.setup is not None:
        if exp.metric.setup.digest is not None:
            setup_digest_sha = _eval_digest(exp.metric.setup.digest, exp_dir)
            entries["metric.setup.digest"] = LockEntry(
                type="digest",
                path=exp_name,
                selector="metric.setup.digest",
                sha256=setup_digest_sha,
            )
        setup_module_path = resolve_output_type_module(
            exp.metric.setup.output,
            require_score_output=False,
            timeout_seconds=exp.metric.setup.timeout_seconds,
            extra_pythonpath=extra_pp,
        )
        setup_module_sha = digest_file(setup_module_path)
        setup_class = exp.metric.setup.output.type.split(":", 1)[1]
        entries["metric.setup.output.type"] = LockEntry(
            type="module",
            path=_rel_module(setup_module_path, exp_dir),
            selector=setup_class,
            sha256=setup_module_sha,
        )
        setup_identity_sha = block_sha256(
            metric_setup_identity_form(exp.metric.setup, setup_digest_sha, setup_module_sha)
        )

    score_digest_sha: str | None = None
    if exp.metric.score.digest is not None:
        score_digest_sha = _eval_digest(exp.metric.score.digest, exp_dir)
        entries["metric.score.digest"] = LockEntry(
            type="digest",
            path=exp_name,
            selector="metric.score.digest",
            sha256=score_digest_sha,
        )
    score_module_sha: str | None = None
    if exp.metric.score.output is not None:
        score_module_path = resolve_output_type_module(
            exp.metric.score.output,
            require_score_output=True,
            timeout_seconds=exp.metric.score.timeout_seconds,
            extra_pythonpath=extra_pp,
        )
        score_module_sha = digest_file(score_module_path)
        score_class = exp.metric.score.output.type.split(":", 1)[1]
        entries["metric.score.output.type"] = LockEntry(
            type="module",
            path=_rel_module(score_module_path, exp_dir),
            selector=score_class,
            sha256=score_module_sha,
        )

    entries["execution"] = LockEntry(
        type="block",
        path=exp_name,
        selector="execution",
        sha256=block_sha256(execution_identity_form(exp.execution)),
    )
    entries["metric"] = LockEntry(
        type="block",
        path=exp_name,
        selector="metric",
        sha256=block_sha256(
            metric_identity_form(
                exp.metric,
                setup_digest_sha256=setup_digest_sha,
                setup_output_module_sha256=setup_module_sha,
                score_digest_sha256=score_digest_sha,
                score_output_module_sha256=score_module_sha,
            )
        ),
    )

    gen_digest_sha: str | None = None
    if exp.generation.digest is not None:
        gen_digest_sha = _eval_digest(exp.generation.digest, exp_dir)
        entries["generation.digest"] = LockEntry(
            type="digest",
            path=exp_name,
            selector="generation.digest",
            sha256=gen_digest_sha,
        )
    entries["generation"] = LockEntry(
        type="block",
        path=exp_name,
        selector="generation",
        sha256=block_sha256(
            generation_identity_form(
                exp.generation,
                generation_digest_sha256=gen_digest_sha,
                experiment_dir=exp_dir,
            )
        ),
    )
    for dotted, (_, ref_path) in loaded.generation_settings_refs.items():
        entries[f"generation.settings.{dotted}"] = LockEntry(
            type="directory" if ref_path.is_dir() else "file",
            path=_rel_to_experiment(ref_path, exp_dir),
            sha256=path_digest(ref_path),
        )

    return ComputedLock(
        loaded=loaded,
        lock=GenerationLock(entries=entries),
        setup_identity_sha256=setup_identity_sha,
        setup_digest_sha256=setup_digest_sha,
        setup_output_module_sha256=setup_module_sha,
        score_digest_sha256=score_digest_sha,
        score_output_module_sha256=score_module_sha,
    )


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def sign_experiment(
    experiment_path: Path,
    *,
    resign: bool = False,
    extra_pythonpath: list[Path] | None = None,
) -> Path:
    """Write generation-experiment.lock.json beside *experiment_path* without editing YAML."""
    experiment_path = experiment_path.resolve(strict=True)
    lock_path = lock_path_for(experiment_path)
    if (lock_path.exists() or lock_path.is_symlink()) and not resign:
        existing_keys: list[str] = []
        try:
            existing = GenerationLock.model_validate(read_json(lock_path))
            existing_keys = sorted(existing.entries.keys())
        except (OSError, ValueError):
            pass
        detail = "\n  ".join(existing_keys) if existing_keys else "(unparseable existing lock)"
        raise ValueError(f"Lock file already exists ({lock_path}); pass --resign to replace existing keys:\n  {detail}")

    computed = compute_lock(experiment_path, extra_pythonpath=extra_pythonpath)
    payload = json_bytes(computed.lock.to_payload())
    _atomic_write_bytes(lock_path, payload)
    return lock_path


def check_experiment(
    experiment_path: Path,
    *,
    extra_pythonpath: list[Path] | None = None,
) -> tuple[ComputedLock, GenerationLock]:
    """Verify generation-experiment.lock.json matches disk without writing any files."""
    experiment_path = experiment_path.resolve(strict=True)
    lock_path = lock_path_for(experiment_path)
    if lock_path.is_symlink() or not lock_path.is_file():
        raise ValueError(f"missing lock file: {lock_path}")
    actual = GenerationLock.model_validate(read_json(lock_path))
    computed = compute_lock(experiment_path, extra_pythonpath=extra_pythonpath)
    expected = computed.lock

    differences: list[str] = []
    missing_keys = sorted(expected.entries.keys() - actual.entries.keys())
    for key in missing_keys:
        differences.append(f"missing lock key: {key}")

    orphan_keys = sorted(actual.entries.keys() - expected.entries.keys())
    for key in orphan_keys:
        differences.append(f"orphan lock key: {key}")

    for key in sorted(expected.entries.keys() & actual.entries.keys()):
        exp_entry = expected.entries[key]
        act_entry = actual.entries[key]
        mismatched_fields = []
        for field in ("type", "path", "selector", "sha256"):
            if getattr(exp_entry, field) != getattr(act_entry, field):
                mismatched_fields.append(
                    f"{field}: lock={getattr(act_entry, field)!r} != disk={getattr(exp_entry, field)!r}"
                )
        if mismatched_fields:
            differences.append(f"{key}: " + "; ".join(mismatched_fields))

    if differences:
        raise ValueError("Lock verification failed:\n  " + "\n  ".join(differences))
    return computed, actual


def verify_lock_entries(
    actual: GenerationLock,
    expected: GenerationLock,
    keys: Iterable[str],
) -> dict[str, LockEntry]:
    """Verify only the lock entries in *keys* and return them."""
    verified: dict[str, LockEntry] = {}
    for key in keys:
        if key not in actual.entries:
            raise ValueError(f"missing lock entry: {key}")
        if key not in expected.entries:
            raise ValueError(f"unexpected lock entry: {key}")
        if actual.entries[key] != expected.entries[key]:
            raise ValueError(f"lock entry differs for {key}")
        verified[key] = actual.entries[key]
    return verified
