# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Assemble an explicitly derived Scaled cohort with retained replacement receipts."""

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from harbor.models.trial.result import TrialResult

from metric_benchmark.config import LoadedCases, PinnedInput, load_cases
from metric_benchmark.io import digest_bytes, digest_file, file_manifest, json_bytes, read_object, write_json
from metric_benchmark.models import Execution, ExecutionManifest, RawResults, Score
from metric_benchmark.scaled_import import (
    Bindings,
    Proof,
    RepairEvidence,
    archive_files,
    extract_native_trial,
    missing_success_evidence,
    observation_measurements,
)


def verify_repair_selection(
    row: Execution, proof: Proof, bindings: Bindings, root: Path, metadata: dict[str, Any], cache: dict
) -> str | None:
    if bindings.repair_source is None:
        raise ValueError("repaired cohort lacks its pinned original results")
    key = ("repair-source", bindings.repair_source.path)
    if key not in cache:
        raw = RawResults.model_validate(read_object(bindings.repair_source.resolve(root)))
        if raw.backend != "scaled_evals" or any(
            r.identity_scheme != "scaled-evals-atom-v1" for r in raw.execution_results
        ):
            raise ValueError("repair source must be an original Scaled cohort")
        cache[key] = {
            (r.agent_bundle_id, r.evaluation.task.key(), r.evaluation.repetition): r for r in raw.execution_results
        }
    original = cache[key].get((row.agent_bundle_id, row.evaluation.task.key(), row.evaluation.repetition))
    agent = bindings.agents[row.agent_bundle_id]
    if original is None or agent.original_run_id != original.agent_run_id or row.agent_run_id == original.agent_run_id:
        raise ValueError("repair cohort must preserve and distinguish the original run identity")
    if original.identity_proof_path is None:
        raise ValueError("original lacks an identity proof")
    original_proof = Proof.model_validate(read_object(root / original.identity_proof_path))
    if proof.repair is None:
        policy_fields = {"grader_policy", "include_verified_grader_reward"}
        if proof.model_dump(exclude=policy_fields) != original_proof.model_dump(exclude=policy_fields):
            raise ValueError("unrepaired native evidence differs from original")
        excluded = {"agent_run_id", "identity_scheme", "identity_proof_path", "metrics"}
        if row.model_dump(exclude=excluded) != original.model_dump(exclude=excluded):
            raise ValueError("unrepaired observation differs from pinned original")
        if row.metrics["reward"] != original.metrics["reward"]:
            raise ValueError("repair assembly changed the original strict reward")
        return agent.original_run_id
    if proof.repair.original_evaluation_id != original.evaluation.evaluation_id:
        raise ValueError("replacement belongs to another original evaluation")
    if digest_file(root / original_proof.metadata_path) != original_proof.metadata_sha256:
        raise ValueError("original metadata changed")
    source_metadata = read_object(root / original_proof.metadata_path)
    old_score = original.metrics.get("verified_grader_reward", original.metrics["reward"])
    old_native = root / original.native_results_path
    if (old_native / "result.json").is_file():
        if file_manifest(old_native) != original_proof.native_files:
            raise ValueError("original native evidence changed")
        old_trial = TrialResult.model_validate(read_object(old_native / "result.json"))
        _, old_metrics = observation_measurements(old_trial, source_metadata, old_native, True, proof.grader_policy)
        old_score = old_metrics["verified_grader_reward"]
    if old_score.value is not None:
        raise ValueError("repair selection cannot replace an available original outcome")
    reproduction = read_object(proof.repair.request.resolve(root))
    created = read_object(proof.repair.creation.resolve(root))
    request = reproduction["request"]
    binding = bindings.tasks[row.evaluation.task.task_id]
    if reproduction["evaluation_id"] != original.evaluation.evaluation_id or created["id"] != metadata["id"]:
        raise ValueError("replacement submission receipts do not bind source and created evaluation")
    for name, expected in {
        "task_id": binding.task_id,
        "task_revision": binding.revision,
        "framework_profile_id": binding.profile_id,
        "framework": "harbor",
        "framework_version": "0.25.0",
        "runtime": "sandbox_k8s",
        "n_attempts": bindings.repetitions,
    }.items():
        if request.get(name) != expected or created.get(name) != expected or metadata.get(name) != expected:
            raise ValueError(f"replacement {name} differs from the pinned execution contract")
    if (
        request["agent_bundle_id"] != agent.bundle_id
        or created["name"] != request["name"]
        or metadata["name"] != request["name"]
    ):
        raise ValueError("replacement agent or submission name differs")
    if any(
        request.get(field)
        for field in ("instruction_prefix", "instruction_postfix", "initial_user_turns", "extra_skill_object_keys")
    ):
        raise ValueError("replacement may not modify task instructions or skills")
    for field in ("credentials", "network_policy", "network_policy_config", "parallelism"):
        if request[field] != source_metadata[field] or metadata[field] != request[field]:
            raise ValueError(f"replacement changes original {field}")
    if metadata["id"] == original.evaluation.evaluation_id or metadata["benchmark_run_id"] is not None:
        raise ValueError("replacement must identify a separate standalone evaluation")
    return None


def assemble(loaded: LoadedCases, original: Path, repairs: Path, output: Path) -> None:
    from metric_benchmark.validation import validate_experiment_results

    if output.exists():
        raise ValueError("repair assembly requires a new output directory")
    validate_experiment_results(loaded, original)
    state = read_object(repairs / "repair-state.json")["members"]
    plan_record = read_object(repairs / "plan.json")
    plan = plan_record["members"]
    planned_ids = [member["original_evaluation_id"] for member in plan]
    if (
        plan_record.get("maximum_new_evaluations_per_member") != 1
        or len(planned_ids) != len(set(planned_ids))
        or set(state) != set(planned_ids)
        or len({member["id"] for member in state.values()}) != len(state)
    ):
        raise ValueError("repair membership must match a unique bounded plan")
    if any(not state.get(m["original_evaluation_id"], {}).get("collected") for m in plan):
        raise ValueError("all planned repair attempts must be collected before assembly")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".scaled-repair-", dir=output.parent) as temporary:
        staged = Path(temporary) / "results"
        shutil.copytree(original, staged)
        root = staged / "cases/case-0000/execution-results"
        source = root / "scaled/repair-original-results.json"
        shutil.copy2(root / "results.json", source)
        raw = RawResults.model_validate(read_object(source))
        if set(planned_ids) - {row.evaluation.evaluation_id for row in raw.execution_results}:
            raise ValueError("repair plan includes an unknown original evaluation")
        bindings = Bindings.model_validate(read_object(root / "scaled/bindings.json"))
        bindings.repair_source = PinnedInput(path=str(source.relative_to(root)), sha256=digest_file(source))
        selection_digest = digest_bytes(json_bytes(state))
        for name, agent in bindings.agents.items():
            agent.original_run_id = agent.run_id
            agent.run_id = f"repair:{selection_digest}:{name}"
        selected: list[dict[str, str]] = []
        for row in raw.execution_results:
            old_id = row.evaluation.evaluation_id
            assert row.identity_proof_path is not None
            proof = Proof.model_validate(read_object(root / row.identity_proof_path))
            metadata = read_object(root / proof.metadata_path)
            native = root / row.native_results_path
            proof.include_verified_grader_reward = True
            proof.grader_policy = "terminal-agent-capacity-v2"
            if (native / "result.json").exists():
                trial = TrialResult.model_validate(read_object(native / "result.json"))
                row.status, row.metrics = observation_measurements(trial, metadata, native, True, proof.grader_policy)
            else:
                row.metrics["verified_grader_reward"] = row.metrics["reward"]
            repair = state.get(old_id)
            if repair and row.metrics["verified_grader_reward"].value is None:
                archive = repairs / "archives" / f"{old_id}.tar.gz"
                if digest_file(archive) != repair["archive_sha256"]:
                    raise ValueError("repair archive digest differs")
                eid = repair["id"]
                metadata = read_object(repairs / "status" / f"{old_id}.json")
                if metadata["id"] != eid:
                    raise ValueError("repair state and metadata disagree")
                files = archive_files(archive)
                reason = "repair archive lacks a unique native trial and provenance"
                row.status = "missing"
                row.native_results_path = f"native/{row.agent_bundle_id}/{eid}/absent"
                row.metrics = {
                    k: Score(value=None, missing_score_reason=reason) for k in ("reward", "verified_grader_reward")
                }
                proof.native_files = {}
                proof.provenance_path = None
                extracted = extract_native_trial(
                    files,
                    root / "native" / row.agent_bundle_id / eid,
                    repetition=row.evaluation.repetition,
                    repetitions=bindings.repetitions,
                )
                if extracted is not None:
                    trial, native = extracted
                    row.native_results_path = native.relative_to(root).as_posix()
                    row.status, row.metrics = observation_measurements(
                        trial, metadata, native, True, proof.grader_policy
                    )
                    proof.native_files = file_manifest(native)
                    missing = missing_success_evidence(trial.exception_info is None, proof.native_files)
                    if missing:
                        reason = "repair archive lacks required ATOM evidence: " + ", ".join(missing)
                        partial = root / "scaled/partial" / eid
                        partial.parent.mkdir(parents=True, exist_ok=True)
                        native.rename(partial)
                        row.native_results_path = f"native/{row.agent_bundle_id}/{eid}/absent"
                        proof.native_files = {}
                        row.status = "missing"
                        row.metrics = {
                            k: Score(value=None, missing_score_reason=reason)
                            for k in ("reward", "verified_grader_reward")
                        }
                    proof.provenance_path = f"scaled/provenance/{eid}.json"
                    write_json(
                        root / proof.provenance_path, json.loads(files["artifacts/scaled-evals-provenance.json"])
                    )
                proof.metadata_path = f"scaled/metadata/{eid}.json"
                write_json(root / proof.metadata_path, metadata)
                proof.metadata_sha256 = digest_file(root / proof.metadata_path)
                proof.archive_sha256 = digest_file(archive)
                pins = {}
                for kind, folder in (("request", "requests"), ("creation", "created")):
                    src = repairs / folder / f"{old_id}.json"
                    if not src.exists() and kind == "request":
                        src = repairs / "preflight-reproduce.json"
                    dest = root / "scaled/repair-receipts" / f"{old_id}-{kind}.json"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    pins[kind] = PinnedInput(path=str(dest.relative_to(root)), sha256=digest_file(dest))
                proof.repair = RepairEvidence(original_evaluation_id=old_id, **pins)
                row.identity_proof_path = f"scaled/proofs/{eid}/repetition-{row.evaluation.repetition:06d}.json"
                row.evaluation = row.evaluation.model_copy(
                    update={
                        "evaluation_id": f"{eid}/repetition-{row.evaluation.repetition:06d}",
                        "execution": metadata["current_execution"],
                    }
                )
                row.outcome_category = metadata.get("outcome", {}).get("category")
                row.unavailable_evidence = {"native_results": reason} if row.status == "missing" else {}
                selected.append({"original": old_id, "replacement": eid})
            row.agent_run_id = bindings.agents[row.agent_bundle_id].run_id
            row.identity_scheme = "scaled-evals-atom-repair-v1"
            # Keep original proof bytes for source verification; derived proofs have their own paths.
            if proof.repair is None:
                row.identity_proof_path = f"scaled/repair-proofs/{old_id}.json"
            write_json(root / row.identity_proof_path, proof)
        (root / "scaled/bindings.json").unlink()
        write_json(root / "scaled/bindings.json", bindings)
        raw.run_id = digest_bytes(json_bytes(bindings))
        (root / "results.json").unlink()
        write_json(root / "results.json", raw)
        manifest = ExecutionManifest.model_validate(read_object(staged / "execution.json"))
        manifest.run_id = raw.run_id
        manifest.execution_sha256 = selection_digest
        (staged / "execution.json").unlink()
        write_json(staged / "execution.json", manifest)
        write_json(
            staged / "repair-selection.json",
            {
                "source": bindings.repair_source.model_dump(),
                "replacements": selected,
                "policy": "terminal-agent-capacity-v2",
                "state_sha256": digest_file(repairs / "repair-state.json"),
            },
        )
        (staged / "validation.json").unlink()
        write_json(staged / "validation.json", validate_experiment_results(loaded, staged))
        staged.rename(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cases", "original", "repairs", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    assemble(load_cases(args.cases), args.original, args.repairs, args.output)


if __name__ == "__main__":
    main()
