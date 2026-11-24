# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Import the qualified pinned ATOM Scaled Evals campaign without relabeling its backend."""

import argparse
import json
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Literal

from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.result import TrialResult
from pydantic import Field

from metric_benchmark.config import Case, LoadedCases, PinnedInput, load_cases
from metric_benchmark.io import digest_bytes, digest_file, file_manifest, json_bytes, read_object, write_json
from metric_benchmark.models import (
    Digest,
    Evaluation,
    Execution,
    ExecutionManifest,
    Model,
    RawResults,
    Score,
    Text,
    relative_path,
    resolve_evidence,
)
from metric_benchmark.validation import native_measurements, task_checksum


class TaskBinding(Model):
    task_id: Text
    revision: int = Field(ge=1)
    image_digest: Digest
    pack_sha256: Digest
    provenance_pack_digest: Literal["required", "unavailable"] = "required"
    pack_path: Text
    source_sha256: Digest
    profile_id: Text
    profile_sha256: Digest


class AgentBinding(Model):
    original_run_id: Text | None = None
    bundle_id: Text
    run_id: Text
    fingerprint: Digest
    image_digest: Text
    source_lock_digest: Digest
    context_sha256: Digest
    context_path: Text


class Bindings(Model):
    repetitions: int = Field(default=1, ge=1)
    repair_source: PinnedInput | None = None
    case_id: Text
    benchmark_id: Text | None
    benchmark_revision: int | None = Field(ge=1)
    tasks: dict[Text, TaskBinding]
    agents: dict[Text, AgentBinding]


class RepairEvidence(Model):
    original_evaluation_id: Text
    request: PinnedInput
    creation: PinnedInput


class Proof(Model):
    grader_policy: Literal["terminal-agent-v1", "terminal-agent-capacity-v2"] = "terminal-agent-v1"
    repair: RepairEvidence | None = None
    binding_path: Text
    metadata_path: Text
    metadata_sha256: Digest
    provenance_path: Text | None = None
    native_files: dict[Text, Digest]
    archive_sha256: Digest | None = None
    include_verified_grader_reward: bool = False


def archive_files(path: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    with tarfile.open(path) as archive:
        for member in archive:
            name = member.name.removeprefix("./").rstrip("/")
            if not name:
                continue
            relative_path(name)
            if name in seen or not (member.isfile() or member.isdir()):
                raise ValueError("unsafe or duplicate archive member")
            seen.add(name)
            if member.isfile():
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("archive member has no content")
                files[name] = stream.read()
    return files


def extract_native_trial(
    files: dict[str, bytes], destination: Path, *, repetition: int = 1, repetitions: int = 1
) -> tuple[TrialResult, Path] | None:
    candidates = []
    for path, content in files.items():
        if path.endswith("/result.json"):
            value = json.loads(content)
            if isinstance(value, dict) and "trial_name" in value:
                candidates.append((path, value))
    candidates.sort(key=lambda item: item[0])
    names = [value["trial_name"] for _, value in candidates]
    if len(names) != len(set(names)):
        raise ValueError("ambiguous duplicate native trial identities")
    if len(candidates) > repetitions:
        raise ValueError("Scaled member contains more native trials than requested repetitions")
    if len(candidates) < repetition or "artifacts/scaled-evals-provenance.json" not in files:
        return None
    path, value = candidates[repetition - 1]
    trial = TrialResult.model_validate(value)
    relative_path(trial.trial_name)
    native = destination / trial.trial_name
    prefix = path.removesuffix("result.json")
    for name, content in files.items():
        if name.startswith(prefix):
            target = native / name[len(prefix) :]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    return trial, native


def prefix_manifest(files: dict[str, bytes], prefix: str) -> dict[str, str]:
    return {name[len(prefix) :]: digest_bytes(data) for name, data in files.items() if name.startswith(prefix)}


def verify_bindings(bindings: Bindings, case: Case, root: Path, input_root: Path) -> None:
    if bindings.repetitions != case.repetitions:
        raise ValueError("Scaled repetition budget differs from prepared case")
    if bindings.case_id != case.case_id or set(bindings.tasks) != set(case.task_sources):
        raise ValueError("Scaled task inventory differs from prepared case")
    if set(bindings.agents) != {agent.agent_bundle_id for agent in case.agents()}:
        raise ValueError("Scaled agent inventory differs from prepared case")
    for task_id, binding in bindings.tasks.items():
        if binding.provenance_pack_digest == "unavailable" and bindings.benchmark_id is not None:
            raise ValueError("benchmark imports require a published task pack digest")
        source = case.task_sources[task_id]
        if binding.source_sha256 != source.sha256:
            raise ValueError("Scaled task source pin differs")
        archive = resolve_evidence(root, binding.pack_path)
        if digest_file(archive) != binding.pack_sha256:
            raise ValueError("Scaled task archive changed")
        files = archive_files(archive)
        expected = file_manifest(source.resolve(input_root))
        if prefix_manifest(files, f"tasks/{task_id}/") != expected:
            raise ValueError("Scaled task pack changes original task bytes")
    for agent in case.agents():
        if agent.harbor.get("import_path") != "atom_runtime:LockedAtomAgent":
            raise ValueError("Scaled importer is qualified only for pinned LockedAtomAgent cases")
        binding = bindings.agents[agent.agent_bundle_id]
        archive = resolve_evidence(root, binding.context_path)
        if digest_file(archive) != binding.context_sha256:
            raise ValueError("Scaled agent context changed")
        files = archive_files(archive)
        descriptor = json.loads(files["descriptor.json"])
        for key in ("fingerprint", "source_lock_digest"):
            if descriptor[key] != getattr(binding, key):
                raise ValueError("Scaled bundle descriptor differs")
        identity = json.loads(files["payload/identity.json"])
        expected_identity = {
            "agent_bundle_id": agent.agent_bundle_id,
            "revision": agent.revision,
            "inputs": {name: value.model_dump() for name, value in agent.inputs.items()},
            "model": agent.harbor["model_name"],
            "settings": agent.harbor["kwargs"],
        }
        if identity != expected_identity:
            raise ValueError("Scaled ATOM identity differs from original configuration")
        prefixes = {
            "source": "payload/baseline/atom/",
            "runtime": "payload/runtime/",
            "dependency_overlay": "payload/dependency-overlay/",
        }
        for name, source in agent.inputs.items():
            if prefix_manifest(files, prefixes[name]) != file_manifest(source.resolve(input_root)):
                raise ValueError("Scaled agent context changes pinned source bytes")


def verify_observation(
    row: Execution,
    binding: TaskBinding,
    agent: AgentBinding,
    metadata: dict[str, Any],
    provenance: dict[str, Any] | None,
    *,
    source_run_id: str | None = "",
) -> None:
    if (
        f"{metadata['id']}/repetition-{row.evaluation.repetition:06d}" != row.evaluation.evaluation_id
        or metadata["benchmark_run_id"]
        != ((None if agent.run_id == metadata["id"] else agent.run_id) if source_run_id == "" else source_run_id)
        or metadata["task_id"] != binding.task_id
        or metadata["task_revision"] != binding.revision
        or metadata["current_execution"] != row.evaluation.execution
        or metadata["framework_profile_id"] != binding.profile_id
    ):
        raise ValueError("Scaled evaluation/run/task/profile/attempt identity differs")
    if not 1 <= row.evaluation.repetition <= metadata.get("n_attempts", 1):
        raise ValueError("Scaled repetition identity exceeds requested count")
    if row.agent_run_id != agent.run_id:
        raise ValueError("Scaled selected run differs")
    if provenance is None:
        if row.status != "missing":
            raise ValueError("available Scaled results require provenance")
        return
    if provenance.get("schema_version") != "scaled-evals-run-provenance-v2":
        raise ValueError("unsupported Scaled provenance version")
    if provenance["evaluation_id"] != metadata["id"] or provenance["status"] != metadata["status"]:
        raise ValueError("Scaled provenance belongs to another evaluation or state")
    task, config = provenance["task"], provenance["config"]
    if (
        task["id"] != binding.task_id
        or task["revision"] != binding.revision
        or task["image_digest"] != binding.image_digest
        or (
            ("sha256:" + task.get("tarball_sha256", "") != binding.pack_sha256)
            if binding.provenance_pack_digest == "required"
            else task.get("tarball_sha256") is not None
        )
    ):
        raise ValueError("Scaled task publication differs from provenance")
    if (
        provenance.get("execution_inputs", {}).get("evaluation", {}).get("n_attempts", 1)
        != metadata.get("n_attempts", 1)
        or config["framework_version"] != "0.25.0"
        or config["framework_profile_id"] != binding.profile_id
        or config["config_hashes"]["framework_config"] != binding.profile_sha256
    ):
        raise ValueError("Scaled framework/profile snapshot differs")
    bundle = config["agent_bundle"]
    for key, expected in (
        ("bundle_id", agent.bundle_id),
        ("image_digest", agent.image_digest),
        ("fingerprint", agent.fingerprint),
        ("source_lock_digest", agent.source_lock_digest),
    ):
        if bundle[key] != expected:
            raise ValueError(f"Scaled bundle {key} differs")
    handle = provenance["runtime"].get("handle")
    if handle != f"{metadata['id']}-x{metadata['current_execution']}":
        raise ValueError("Scaled archive belongs to another execution attempt")


def verify_scaled_identity(
    row: Execution,
    case: Case,
    root: Path,
    input_root: Path,
    trial: TrialResult | None,
    cache: dict,
    *,
    allow_unsigned: bool = False,
) -> None:
    if allow_unsigned or row.identity_proof_path is None:
        raise ValueError("Scaled import requires pinned inputs and an identity proof")
    proof = Proof.model_validate(read_object(resolve_evidence(root, row.identity_proof_path)))
    key = ("scaled", proof.binding_path)
    if key not in cache:
        bindings = Bindings.model_validate(read_object(resolve_evidence(root, proof.binding_path)))
        verify_bindings(bindings, case, root, input_root)
        cache[key] = bindings
    bindings = cache[key]
    binding = bindings.tasks[row.evaluation.task.task_id]
    agent = bindings.agents[row.agent_bundle_id]
    path = resolve_evidence(root, proof.metadata_path)
    if digest_file(path) != proof.metadata_sha256:
        raise ValueError("Scaled evaluation metadata changed")
    metadata = read_object(path)
    provenance = read_object(resolve_evidence(root, proof.provenance_path)) if proof.provenance_path else None
    source_run_id = None if agent.run_id == metadata["id"] else agent.run_id
    if row.identity_scheme == "scaled-evals-atom-repair-v1":
        from metric_benchmark.scaled_repair import verify_repair_selection

        source_run_id = verify_repair_selection(row, proof, bindings, root, metadata, cache)
    elif bindings.repair_source is not None or agent.original_run_id is not None or proof.repair is not None:
        raise ValueError("repair evidence requires its explicit identity scheme")
    verify_observation(row, binding, agent, metadata, provenance, source_run_id=source_run_id)
    if trial is None:
        return
    native = resolve_evidence(root, row.native_results_path)
    if file_manifest(native) != proof.native_files:
        raise ValueError("Scaled native evidence changed")
    if trial.config.task.path is None or not trial.config.task.path.is_absolute():
        raise ValueError("Scaled native task path must be absolute")
    task_id = row.evaluation.task.task_id
    checksum_key = ("scaled-task-checksum", task_id)
    if checksum_key not in cache:
        cache[checksum_key] = task_checksum(HarborTask(case.task_sources[task_id].resolve(input_root)))
    if trial.task_name.rsplit("/", 1)[-1] != task_id or trial.task_checksum != cache[checksum_key]:
        raise ValueError("Scaled native task bytes or name differ from original task")
    original = next(a for a in case.agents() if a.agent_bundle_id == row.agent_bundle_id)
    if (
        trial.config.agent.model_name != original.harbor["model_name"]
        or trial.config.agent.import_path != "harbor.agents.installed.matrix_command_agent:MatrixCommandAgent"
        or trial.config.agent.kwargs.get("run_command") != "set -o pipefail; /installed-agent/bin/atom"
        or trial.config.agent.override_timeout_sec is not None
    ):
        raise ValueError("Scaled native adapter, model or timeout differs")
    source_path = native / "agent/source-identity.json"
    if source_path.exists():
        source = read_object(source_path)
        settings = original.harbor["kwargs"]
        if not isinstance(settings, dict):
            raise ValueError("ATOM settings must be an object")
        if (
            source["source_sha256"] != settings["source_sha256"]
            or source["variant_id"] != row.agent_bundle_id
            or source["python"] != settings["python_version"]
            or source["uv_lock_sha256"] != agent.source_lock_digest
        ):
            raise ValueError("Scaled installed ATOM source differs")
        dependencies = read_object(original.inputs["runtime"].resolve(input_root) / "dependencies.json")
        if any(
            origin.get("vcs_info", {}).get("commit_id") != dependencies["revision"]
            for origin in source.get("library_origins", {}).values()
        ) or set(source.get("library_origins", {})) != {"nooa", "nooa-cli", "nooa-bench"}:
            raise ValueError("Scaled shared library source revision differs")
        overlay = settings.get("dependency_overlay_sha256")
        derived = source.get("derived_libraries", {})
        if (overlay is None and derived) or (
            overlay is not None and derived.get("nooa", {}).get("overlay_sha256") != overlay
        ):
            raise ValueError("Scaled installed dependency overlay differs")
        identity = read_object(resolve_evidence(native, "agent/bundle-identity.json"))
        if identity["settings"] != settings or identity["model"] != original.harbor["model_name"]:
            raise ValueError("Scaled runtime model/settings differ")
        context_path = native / "agent/model-context.json"
        if context_path.exists():
            context = read_object(context_path)
            if (
                context["model"] != original.harbor["model_name"]
                or context["declared_context_window"] != settings["context_window"]
                or context["effective_context_window"] != settings["context_window"]
            ):
                raise ValueError("Scaled effective model context differs")
        elif trial.exception_info is None:
            raise ValueError("successful Scaled trial lacks model context evidence")
    elif trial.exception_info is None:
        raise ValueError("successful Scaled ATOM trial lacks source identity")
    if metadata.get("n_attempts", 1) > 1:
        if metadata["status"] not in ("succeeded", "failed", "cancelled"):
            raise ValueError("Scaled evaluation is not terminal")
        return
    if metadata["status"] == "succeeded":
        if trial.exception_info is not None or row.metrics.get(
            "reward", Score(value=None, missing_score_reason="absent")
        ).value != metadata.get("reward"):
            raise ValueError("Scaled service/native reward disagreement")
    elif row.status != "error":
        raise ValueError("failed Scaled execution must not become a valid reward")


def prepare_bindings(campaign: Path, collection: Path, case: Case, root: Path) -> Bindings:
    bindings = Bindings.model_validate(read_object(campaign / "bindings.json"))
    for relative in [
        *(t.pack_path for t in bindings.tasks.values()),
        *(a.context_path for a in bindings.agents.values()),
    ]:
        source = resolve_evidence(campaign, relative)
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return bindings


def missing_success_evidence(completed: bool, files: dict[str, str]) -> list[str]:
    if not completed:
        return []
    required = (
        "agent/source-identity.json",
        "agent/bundle-identity.json",
        "agent/model-context.json",
        "agent/trajectory.json",
    )
    return [path for path in required if path not in files]


def scaled_measurements(trial: TrialResult, metadata: dict[str, Any]) -> tuple[str, dict[str, Score]]:
    if metadata.get("n_attempts", 1) > 1:
        if metadata["status"] not in ("succeeded", "failed", "cancelled"):
            raise ValueError("Scaled evaluation is not terminal")
        return native_measurements(trial)
    if metadata["status"] != "succeeded":
        reason = "Scaled execution failed: " + str(metadata.get("last_failure_code") or metadata["status"])
        return "error", {"reward": Score(value=None, missing_score_reason=reason)}
    return native_measurements(trial)


def observation_measurements(
    trial: TrialResult,
    metadata: dict[str, Any],
    native: Path,
    include_verified_grader_reward: bool,
    grader_policy: str = "terminal-agent-v1",
) -> tuple[str, dict[str, Score]]:
    status, metrics = scaled_measurements(trial, metadata)
    if include_verified_grader_reward:
        from metric_benchmark.grader_outcomes import completed_grader_reward, retained_grader_reward

        if status == "succeeded":
            metrics["verified_grader_reward"] = (
                completed_grader_reward(trial, native)
                if grader_policy == "terminal-agent-capacity-v2"
                else metrics["reward"]
            )
        elif trial.exception_info is not None and (
            metadata.get("n_attempts", 1) > 1
            or metadata.get("last_failure_code") == trial.exception_info.exception_type
        ):
            metrics["verified_grader_reward"] = retained_grader_reward(
                trial,
                native,
                metadata["status"],
                allow_capacity_failure=grader_policy == "terminal-agent-capacity-v2",
                allow_completed_member=metadata.get("n_attempts", 1) > 1,
            )
        else:
            metrics["verified_grader_reward"] = Score(
                value=None, missing_score_reason="service/native failure classifications differ"
            )
    return status, metrics


def verified_scaled_measurements(row: Execution, root: Path, trial: TrialResult) -> tuple[str, dict[str, Score]]:
    if row.identity_proof_path is None:
        raise ValueError("Scaled results require an identity proof")
    proof = Proof.model_validate(read_object(resolve_evidence(root, row.identity_proof_path)))
    return observation_measurements(
        trial,
        read_object(resolve_evidence(root, proof.metadata_path)),
        resolve_evidence(root, row.native_results_path),
        proof.include_verified_grader_reward,
        proof.grader_policy,
    )


def import_results(
    loaded: LoadedCases, campaign: Path, collection: Path, output: Path, *, include_verified_grader_reward: bool = False
) -> None:
    if output.exists():
        raise ValueError("use a new import output directory; historical imports are immutable")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".scaled-import-", dir=output.parent) as temporary:
        staged = Path(temporary) / "results"
        _import_results(
            loaded, campaign, collection, staged, include_verified_grader_reward=include_verified_grader_reward
        )
        staged.rename(output)


def _import_results(
    loaded: LoadedCases, campaign: Path, collection: Path, output: Path, *, include_verified_grader_reward: bool = False
) -> None:
    if output.exists():
        raise ValueError("use a new import output directory; historical imports are immutable")
    if len(loaded.cases) != 1:
        raise ValueError("qualified ATOM importer accepts one prepared case")
    loaded.identity_payload(verify=True)
    prepared = next(iter(loaded.cases.values()))
    case = prepared.case
    root = output / "cases/case-0000/execution-results"
    bindings = prepare_bindings(campaign, collection, case, root)
    verify_bindings(bindings, case, root, prepared.root)
    write_json(root / "scaled/bindings.json", bindings)
    manifest = read_object(collection / "manifest.json")
    if "matrix" not in manifest or digest_file(collection / "matrix.csv") != manifest["matrix"]["sha256"]:
        raise ValueError("Scaled collection is incomplete or its matrix changed")
    by_remote = {binding.task_id: task for task, binding in bindings.tasks.items()}
    tasks = {task.task_id: task for task in case.task_inventory.tasks}
    rows = []
    for name, agent in bindings.agents.items():
        evaluations = read_object(collection / f"metadata/evaluations/{name}.json")["data"]
        if len(evaluations) != len(tasks) or {r["task_id"] for r in evaluations} != set(by_remote):
            raise ValueError("collected Scaled membership is incomplete or duplicated")
        for metadata in evaluations:
            if metadata.get("n_attempts", 1) != case.repetitions:
                raise ValueError("Scaled requested repetition count differs from case")
            eid = metadata["id"]
            record = manifest["downloads"][eid]
            archive_digest = None
            files: dict[str, bytes] = {}
            if record["status"] == "downloaded":
                archive = resolve_evidence(collection, record["path"])
                archive_digest = digest_file(archive)
                if archive_digest != record["sha256"]:
                    raise ValueError("collected archive hash differs")
                files = archive_files(archive)
            for repetition in range(1, case.repetitions + 1):
                eid = metadata["id"]
                task_id = by_remote[metadata["task_id"]]
                record = manifest["downloads"][eid]
                if record["current_execution"] != metadata["current_execution"]:
                    raise ValueError("collected archive execution is stale")
                metadata_path = f"scaled/metadata/{eid}.json"
                relative_path(metadata_path)
                write_json(root / metadata_path, metadata)
                native_path = f"native/{name}/{eid}/absent-{repetition}"
                status = "missing"
                reason = "native archive unavailable: " + str(record.get("error") or record["status"])
                metrics = {"reward": Score(value=None, missing_score_reason=reason)}
                native_files = {}
                provenance_path = None
                if record["status"] == "downloaded":
                    extracted = extract_native_trial(
                        files, root / "native" / name / eid, repetition=repetition, repetitions=case.repetitions
                    )
                    if extracted is not None:
                        trial, native = extracted
                        native_path = native.relative_to(root).as_posix()
                        status, metrics = observation_measurements(
                            trial, metadata, native, include_verified_grader_reward, "terminal-agent-capacity-v2"
                        )
                        native_files = file_manifest(native)
                        missing_files = missing_success_evidence(trial.exception_info is None, native_files)
                        if missing_files:
                            reason = "native archive lacks required ATOM evidence: " + ", ".join(missing_files)
                            partial = root / "scaled/partial" / eid / str(repetition)
                            partial.parent.mkdir(parents=True, exist_ok=True)
                            native.rename(partial)
                            native_path = f"native/{name}/{eid}/absent-{repetition}"
                            native_files = {}
                            status = "missing"
                            metrics = {"reward": Score(value=None, missing_score_reason=reason)}
                        provenance_path = f"scaled/provenance/{eid}.json"
                        write_json(root / provenance_path, json.loads(files["artifacts/scaled-evals-provenance.json"]))
                    else:
                        reason = "archive lacks a unique native trial and provenance"
                        metrics = {"reward": Score(value=None, missing_score_reason=reason)}
                if include_verified_grader_reward and "verified_grader_reward" not in metrics:
                    metrics["verified_grader_reward"] = metrics["reward"]
                proof_path = f"scaled/proofs/{eid}/repetition-{repetition:06d}.json"
                write_json(
                    root / proof_path,
                    Proof(
                        binding_path="scaled/bindings.json",
                        metadata_path=metadata_path,
                        metadata_sha256=digest_file(root / metadata_path),
                        provenance_path=provenance_path,
                        native_files=native_files,
                        archive_sha256=archive_digest,
                        include_verified_grader_reward=include_verified_grader_reward,
                        grader_policy="terminal-agent-capacity-v2",
                    ),
                )
                rows.append(
                    Execution(
                        agent_run_id=agent.run_id,
                        agent_bundle_id=name,
                        evaluation=Evaluation(
                            evaluation_id=f"{eid}/repetition-{repetition:06d}",
                            repetition=repetition,
                            execution=metadata["current_execution"],
                            task=tasks[task_id],
                        ),
                        status=status,
                        outcome_category=metadata.get("outcome", {}).get("category"),
                        metrics=metrics,
                        native_results_path=native_path,
                        native_results_format="directory",
                        unavailable_evidence={"native_results": reason} if status == "missing" else {},
                        identity_proof_path=proof_path,
                        identity_scheme="scaled-evals-atom-v1",
                    )
                )
    run_id = digest_bytes(json_bytes(bindings))
    raw = RawResults(
        backend="scaled_evals", run_id=run_id, task_inventory=case.task_inventory.tasks, execution_results=rows
    )
    write_json(root / "results.json", raw)
    write_json(
        output / "execution.json",
        ExecutionManifest(
            backend="scaled_evals",
            cases_sha256=loaded.identity,
            execution_sha256=digest_file(collection / "manifest.json"),
            run_id=run_id,
            cases={case.case_id: "cases/case-0000/execution-results/results.json"},
        ),
    )
    from metric_benchmark.validation import validate_experiment_results

    write_json(output / "validation.json", validate_experiment_results(loaded, output))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cases", "campaign", "collection", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--include-verified-grader-reward", action="store_true")
    args = parser.parse_args()
    import_results(
        load_cases(args.cases),
        args.campaign,
        args.collection,
        args.output,
        include_verified_grader_reward=args.include_verified_grader_reward,
    )


if __name__ == "__main__":
    main()
