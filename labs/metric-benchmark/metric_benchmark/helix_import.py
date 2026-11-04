# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Import complete Helix evidence into the shared, validated results contract."""

import argparse
import shutil
import tempfile
from importlib import import_module
from pathlib import Path

from harbor.models.trial.result import TrialResult

from metric_benchmark.config import load_cases
from metric_benchmark.execution import case_root
from metric_benchmark.helix import _object
from metric_benchmark.helix_identity import HelixIdentity, collection_record
from metric_benchmark.helix_publication import PublicationPlan
from metric_benchmark.io import cli, digest_bytes, digest_file, file_manifest, json_bytes, read_json, write_json
from metric_benchmark.models import Evaluation, Execution, ExecutionManifest, RawResults, resolve_evidence
from metric_benchmark.validation import native_measurements, validate_experiment_results, validate_results


def import_helix_results(loaded, publication: Path, collection: Path, output: Path):
    loaded.identity_payload(verify=True)
    record, submission = collection_record(collection)
    plan = PublicationPlan.model_validate(read_json(resolve_evidence(publication, "publication.json")))
    if loaded.identity != plan.cases_sha256 or loaded.identity != submission.cases_sha256:
        raise ValueError("prepared, published and collected case identities differ")
    identity = digest_bytes(
        json_bytes(
            {
                "publication": plan.model_dump(),
                "collection_sha256": digest_file(collection / "collection.json"),
                "import_contract": "helix-image-binding-v1",
            }
        )
    )
    if output.exists():
        receipt = _object(resolve_evidence(output, "import.json"))
        files = file_manifest(output)
        files.pop("import.json", None)
        if receipt != {"execution_sha256": identity, "files": files}:
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
        staged = Path(temporary) / "results"
        indexes = {}
        for case_index, (case_id, prepared) in enumerate(loaded.cases.items()):
            root = case_root(staged, case_index)
            evidence = root / "helix"
            shutil.copytree(collection, evidence / "collection")
            write_json(evidence / "publication.json", plan)
            published_case = next(c for c in plan.cases if c.case_id == case_id)
            rows = []
            for index, observation in enumerate(record["observations"]):
                if observation["case_id"] != case_id:
                    continue
                task_id = observation["task_id"]
                task = next(t for t in prepared.case.task_inventory.tasks if t.task_id == task_id)
                published_task = next(t for t in published_case.tasks if t.task_id == task_id)
                proof_root = evidence / f"identities/observation-{index:04d}"
                source = prepared.case.task_sources[task_id].resolve(prepared.root)
                effective = proof_root / "effective" / source.name
                shutil.copytree(source, effective)
                archive = resolve_evidence(publication, f"archives/{published_task.name}.tar.gz")
                shutil.copyfile(archive, proof_root / "source.tar.gz")
                job = _object(resolve_evidence(collection, observation["job_spec_path"]))
                environment = job["spec"]["environment"]
                if environment.get("verifier") is not None or environment.get("compose_images"):
                    raise ValueError("separate verifier and compose image mappings are not qualified")
                bind_image(effective, environment["agent"]["image_ref"])
                native_path = "helix/collection/" + observation["native_results_path"]
                trial = TrialResult.model_validate(read_json(resolve_evidence(root, native_path + "/result.json")))
                status, metrics = native_measurements(trial)
                proof = HelixIdentity(
                    collection_path="helix/collection",
                    publication_path="helix/publication.json",
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
                        helix_identity_path=proof_path.relative_to(root).as_posix(),
                    )
                )
            raw = RawResults(
                run_id=submission.run_id, task_inventory=prepared.case.task_inventory.tasks, execution_results=rows
            )
            validate_results(raw, prepared.case, root, prepared.root)
            write_json(root / "results.json", raw)
            indexes[case_id] = (root / "results.json").relative_to(staged).as_posix()
        manifest = ExecutionManifest(
            cases_sha256=loaded.identity, execution_sha256=identity, run_id=submission.run_id, cases=indexes
        )
        write_json(staged / "execution.json", manifest)
        validate_experiment_results(loaded, staged)
        write_json(staged / "import.json", {"execution_sha256": identity, "files": file_manifest(staged)})
        if output.exists():
            raise ValueError("import destination was created by another writer")
        staged.rename(output)
    return manifest.model_dump()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--publication", required=True, type=Path)
    parser.add_argument("--collection", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)

    def run(args):
        result = import_helix_results(load_cases(args.cases), args.publication, args.collection, args.output)
        print(f"Imported {len(result['cases'])} validated cases.")

    cli(parser, run)
