# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Import complete Helix evidence into the shared, validated results contract."""

import shutil
import tempfile
from importlib import import_module
from importlib.metadata import version
from pathlib import Path

from harbor.models.trial.result import TrialResult

from metric_benchmark.execution import case_root
from metric_benchmark.helix import _object
from metric_benchmark.helix_identity import HelixIdentity, collection_record
from metric_benchmark.helix_publication import PublicationPlan
from metric_benchmark.io import digest_bytes, digest_file, file_manifest, json_bytes, read_json, write_json
from metric_benchmark.models import Evaluation, Execution, ExecutionManifest, RawResults, Score, resolve_evidence
from metric_benchmark.validation import native_measurements, validate_experiment_results


def import_helix_results(loaded, publication: Path, collection: Path, output: Path):
    try:
        return _import_helix_results(loaded, publication, collection, output)
    except (KeyError, IndexError, TypeError, StopIteration) as error:
        raise ValueError(f"malformed Helix import evidence: {type(error).__name__}") from None


def _import_helix_results(loaded, publication: Path, collection: Path, output: Path):
    if any(prepared.allow_unsigned for prepared in loaded.cases.values()):
        raise ValueError("Helix import requires fully signed prepared inputs")
    loaded.identity_payload(verify=True)
    record, submission = collection_record(collection)
    plan = PublicationPlan.model_validate(read_json(resolve_evidence(publication, "publication.json")))
    if loaded.identity != plan.cases_sha256 or loaded.identity != submission.cases_sha256:
        raise ValueError("prepared, published and collected case identities differ")
    if plan != submission.publication:
        raise ValueError("publication differs from the submitted publication identity")
    provenance = {
        "publication": _object(resolve_evidence(publication, "provenance.json")),
        "collection": _object(resolve_evidence(collection, "provenance.json")),
    }
    if provenance["publication"]["client_versions"].get("harbor") != "0.24.0":
        raise ValueError("publication requires the qualified Harbor 0.24.0 client")
    identity = digest_bytes(
        json_bytes(
            {
                "publication": plan.model_dump(),
                "collection_sha256": digest_file(collection / "collection.json"),
                "import_contract": "helix-task-image-v2",
            }
        )
    )
    if output.exists():
        receipt = _object(resolve_evidence(output, "import.json"))
        if receipt.get("importer_versions", {}).get("harbor") not in ("0.20.0", "0.24.0"):
            raise ValueError("unsupported recorded Harbor import version")
        files = file_manifest(output)
        files.pop("import.json", None)
        if (
            receipt.get("execution_sha256") != identity
            or receipt.get("files") != files
            or receipt.get("provenance") != provenance
        ):
            raise ValueError("import inputs or completed evidence changed")
        validate_experiment_results(loaded, output)
        return _object(output / "execution.json")
    try:
        bind_image = import_module("nemo_evals.jobs.harbor_task_dispatch").bind_task_image
    except ImportError:
        raise ValueError(
            "initial import requires the pinned Helix client environment; offline validation does not"
        ) from None
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".helix-import-", dir=output.parent) as temporary:
        staged = Path(temporary).resolve() / "results"
        indexes = {}
        for case_index, (case_id, prepared) in enumerate(loaded.cases.items()):
            root = case_root(staged, case_index)
            evidence = root / "helix"
            copy_case_collection(collection, evidence / "collection", record, submission, case_id)
            published_case = next(c for c in plan.cases if c.case_id == case_id)
            rows = []
            for index, observation in enumerate(record["observations"]):
                if observation["case_id"] != case_id:
                    continue
                task_id = observation["task_id"]
                task = next(t for t in prepared.case.task_inventory.tasks if t.task_id == task_id)
                published_task = next(t for t in published_case.tasks if t.task_id == task_id)
                native_path = "helix/collection/" + observation["native_results_path"]
                unavailable = observation.get("unavailable_evidence", {})
                if observation["status"] == "missing":
                    proof_root = evidence / "identities/missing"
                    proof = HelixIdentity(collection_path="helix/collection")
                    status = "missing"
                    metrics = {"reward": Score(value=None, missing_score_reason=unavailable["native_results"])}
                else:
                    job = _object(resolve_evidence(collection, observation["job_spec_path"]))
                    environment = job["spec"]["environment"]
                    image = environment["agent"]["image_ref"]
                    archive = resolve_evidence(publication, f"archives/{published_task.name}.tar.gz")
                    derivation = digest_bytes(
                        json_bytes(
                            {"task": published_task.model_dump(), "image": image, "archive": digest_file(archive)}
                        )
                    )[7:]
                    proof_root = evidence / "identities" / derivation
                    source = prepared.case.task_sources[task_id].resolve(prepared.root)
                    effective = proof_root / "effective" / source.name
                    if not effective.exists():
                        shutil.copytree(source, effective)
                        shutil.copyfile(archive, proof_root / "source.tar.gz")
                        bind_image(effective, image)
                    trial = TrialResult.model_validate(read_json(resolve_evidence(root, native_path + "/result.json")))
                    status, metrics = native_measurements(trial)
                    proof = HelixIdentity(
                        collection_path="helix/collection",
                        source_archive_path=(proof_root / "source.tar.gz").relative_to(root).as_posix(),
                        effective_task_path=effective.relative_to(root).as_posix(),
                    )
                proof_path = proof_root / "identity.json"
                write_json(proof_path, proof)
                rows.append(
                    Execution(
                        agent_run_id=observation["remote_run_id"],
                        agent_bundle_id=observation["agent_bundle_id"],
                        evaluation=Evaluation(
                            evaluation_id=f"{observation['remote_run_id']}/{task_id}", execution=1, task=task
                        ),
                        status=status,
                        metrics=metrics,
                        native_results_path=native_path,
                        native_results_format="directory",
                        identity_proof_path=proof_path.relative_to(root).as_posix(),
                        identity_scheme="helix-task-image-v2",
                        unavailable_evidence=unavailable,
                    )
                )
            raw = RawResults(
                backend="helix",
                run_id=submission.run_id,
                task_inventory=prepared.case.task_inventory.tasks,
                execution_results=rows,
            )
            write_json(root / "results.json", raw)
            indexes[case_id] = (root / "results.json").relative_to(staged).as_posix()
        manifest = ExecutionManifest(
            backend="helix",
            cases_sha256=loaded.identity,
            execution_sha256=identity,
            run_id=submission.run_id,
            cases=indexes,
        )
        write_json(staged / "execution.json", manifest)
        validate_experiment_results(loaded, staged)
        importer_versions = {"harbor": version("harbor")}
        if any(agent.registered is not None for prepared in loaded.cases.values() for agent in prepared.case.agents()):
            for package in ("nemo-agents-plugin", "nemo-evals-plugin", "nemo-fabric"):
                importer_versions[package] = version(package)
        write_json(
            staged / "import.json",
            {
                "execution_sha256": identity,
                "files": file_manifest(staged),
                "provenance": provenance,
                "importer_versions": importer_versions,
            },
        )
        if output.exists():
            raise ValueError("import destination was created by another writer")
        staged.rename(output)
    return manifest.model_dump()


def copy_case_collection(source, destination, record, submission, case_id):
    prefixes = tuple(f"runs/run-{i:04d}/" for i, run in enumerate(submission.runs) if run.case_id == case_id)
    for name in record["files"]:
        if not name.startswith("runs/") or name.startswith(prefixes):
            path = resolve_evidence(source, name)
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    write_json(destination / "source_collection.json", record)
    scoped = {
        **record,
        "scope_case_id": case_id,
        "observations": [row for row in record["observations"] if row["case_id"] == case_id],
        "files": file_manifest(destination),
    }
    write_json(destination / "collection.json", scoped)
