# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Collect selected Helix runs directly into the shared results contract."""

import argparse
import shutil
from importlib import import_module
from pathlib import Path

from metric_benchmark.helix import NotReady, Submission, check_helix_runs, download_helix_results
from metric_benchmark.helix_client import HelixClient
from metric_benchmark.helix_execution import RunReceipt, capture_run, captured_evidence, save_receipt
from metric_benchmark.helix_import import import_helix_results
from metric_benchmark.helix_sources import download_sources, load_references, source_destination
from metric_benchmark.io import digest_bytes, file_manifest, json_bytes, read_object, write_json


def selected_submission(references, receipts):
    expected = [
        (case.case_id, agent.agent_bundle_id) for case in references.publication.definitions for agent in case.agents()
    ]
    selected = {(receipt.case_id, receipt.agent_bundle_id): receipt for receipt in receipts}
    if len(selected) != len(receipts) or set(selected) != set(expected):
        raise ValueError("select exactly one run for every prepared baseline and mutant")
    receipts = [selected[key] for key in expected]
    for receipt in receipts:
        if receipt.references != references:
            raise ValueError("run receipt differs from selected publication references")
        if receipt.execution != receipts[0].execution:
            raise ValueError("selected runs use different execution settings")
        if receipt.response is None:
            raise ValueError("selected run has not been accepted; resume execution")
    plans = [receipt.submission() for receipt in receipts]
    identity = digest_bytes(json_bytes([receipt.request for receipt in receipts]))[7:]
    combined = {
        **plans[0].model_dump(),
        "run_id": identity,
        "selection": None,
        "runs": [plan.runs[0].model_dump() for plan in plans],
    }
    return Submission.model_validate(combined), receipts


def collect_results(references, receipts, sources, output, client):
    plan, receipts = selected_submission(references, receipts)
    client.require_destination(plan.execution.base_url, plan.execution.workspace)
    work = output.parent / f".{output.name}.helix"
    if work.is_symlink():
        raise ValueError("collection work directory must not be a symlink")
    write_json(work / "submission/submission.json", plan)
    for index, receipt in enumerate(receipts):
        write_json(work / f"submission/runs/run-{index:04d}.json", receipt.response)
        # A portable receipt preserves the captured configuration and file hashes.
        # Restore matching retained source bytes instead of depending on run-snapshot TTL.
        retained = work / f"submission/resolved/run-{index:04d}"
        if receipt.agent_bundle_id in plan.bindings.cases[receipt.case_id].registrations:
            if not receipt.resolved:
                capture_run(receipt, client)
            for name, value in receipt.resolved.items():
                if name != "agent_files":
                    write_json(source_destination(retained, name), value)
            if "agent_files" in receipt.resolved and not (retained / "agent-files").exists():
                download_sources(
                    references,
                    sources,
                    client,
                    resources={"agents"},
                    selection=(receipt.case_id, receipt.agent_bundle_id),
                )
                prepared = references.inputs(sources).case(receipt.case_id)
                agent = next(a for a in prepared.case.agents() if a.agent_bundle_id == receipt.agent_bundle_id)
                bundle = agent.inputs[agent.registered.bundle_input].resolve(sources)
                if file_manifest(bundle) != receipt.resolved["agent_files"]:
                    raise ValueError("retained agent source differs from captured run files")
                shutil.copytree(bundle, retained / "agent-files")
    # Pending results leave the published output directory absent.
    check_helix_runs(work / "submission", client)
    for index, receipt in enumerate(receipts):
        retained = work / f"submission/resolved/run-{index:04d}"
        if retained.is_dir():
            receipt.resolved = captured_evidence(retained)
    download_sources(references, sources, client)
    publication = work / "publication"
    write_json(publication / "publication.json", references.publication)
    write_json(publication / "provenance.json", references.provenance)
    for case in references.publication.cases:
        prepared = references.inputs(sources).case(case.case_id)
        for task in case.tasks:
            archive = publication / "archives" / f"{task.name}.tar.gz"
            if not archive.exists():
                archive.parent.mkdir(parents=True, exist_ok=True)
                source = prepared.case.task_sources[task.task_id].resolve(sources)
                import_module("nemo_evals.harbor.archive").pack_task(source, archive)
    collection = work / "collection"
    download_helix_results(work / "submission", collection, client)
    return import_helix_results(references.inputs(sources), publication, collection, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True, action="append")
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--sources-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        references = load_references(args.references)
        receipts = [RunReceipt.model_validate(read_object(path)) for path in args.run]
        client = HelixClient.from_settings(args.client)
        try:
            result = collect_results(references, receipts, args.sources_output, args.output, client)
            for path, receipt in zip(args.run, receipts, strict=True):
                save_receipt(path, receipt)
            print(f"Collected and verified {len(result['cases'])} cases.")
        finally:
            client.client.close()
    except NotReady as error:
        parser.exit(2, f"{error}\n")
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")
