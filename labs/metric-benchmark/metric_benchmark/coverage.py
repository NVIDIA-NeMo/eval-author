# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Invoke a supplied coverage system using a fixed, baseline-only file contract."""

import argparse
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from metric_benchmark.config import CoverageConfig, file_manifest
from metric_benchmark.io import cli, digest_bytes, digest_file, json_bytes, read_json, write_json
from metric_benchmark.models import Artifact, Digest, Model, RawResults, Score, Subsets, Text, resolve_evidence
from metric_benchmark.scoring import Context, ScoringConfig, context, identity, select_executions


class CoverageScores(Artifact):
    artifact_type: Literal["coverage_scores"] = "coverage_scores"
    context: Context
    configuration: CoverageConfig
    case_description_sha256: Digest
    scores: dict[Text, Score]
    receipts: dict[Text, Digest]


def copy_evidence(source: Path, destination: Path, native_format: str) -> dict[str, str]:
    if native_format == "directory":
        before = file_manifest(source)
        shutil.copytree(source, destination)
        after = file_manifest(destination)
    else:
        if source.is_symlink() or not source.is_file():
            raise ValueError("archive must be an ordinary file")
        before = {"archive": digest_file(source)}
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        after = {"archive": digest_file(destination)}
    if before != after:
        raise ValueError("evidence changed while copying")
    return before


class Receipt(Model):
    request_sha256: Digest
    configuration_sha256: Digest
    files: dict[Text, Digest]
    score: Score


def invoke_coverage(
    raw: RawResults,
    subsets: Subsets,
    selection: ScoringConfig,
    configuration: CoverageConfig,
    case_description: dict,
    record_root: Path,
    work_root: Path,
    *,
    cached_only: bool = False,
) -> CoverageScores:
    ctx = context(raw, subsets, selection)
    selected = select_executions(raw, selection)
    executable = Path(configuration.command[0])
    if not executable.is_absolute():
        raise ValueError("coverage command must start with an absolute executable path")
    work_root.mkdir(parents=True, exist_ok=True)
    if work_root.is_symlink():
        raise ValueError("coverage work root must not be a symlink")
    scores, receipts = {}, {}
    # Request IDs reveal no mutant labels, rewards, scores, or run identities.
    for ordinal, subset in enumerate(sorted(subsets.eval_subsets, key=lambda s: s.eval_subset_id)):
        destination = work_root / f"subset-{ordinal + 1:06d}"
        with tempfile.TemporaryDirectory(prefix=".pending-", dir=work_root) as temporary:
            workspace = Path(temporary)
            evidence = []
            for index, task in enumerate(sorted(subset.tasks, key=lambda t: t.key())):
                row = selected.get((selection.baseline_bundle_id, *task.key()))
                if row is None or row.status not in selection.completed_statuses:
                    raise ValueError(f"baseline evidence unavailable for {task.task_id}")
                source = resolve_evidence(record_root, row.native_results_path)
                relative = f"baseline/task-{index + 1:06d}"
                if row.native_results_format == "tar.gz":
                    relative += ".tar.gz"
                hashes = copy_evidence(source, workspace / relative, row.native_results_format)
                evidence.append(
                    {"task": task.model_dump(), "path": relative, "format": row.native_results_format, "files": hashes}
                )
            request = {
                "schema_version": 1,
                "case_description": case_description,
                "eval_subset_id": subset.eval_subset_id,
                "tasks": [t.model_dump() for t in sorted(subset.tasks, key=lambda t: t.key())],
                "baseline_evidence": evidence,
                "settings": configuration.settings,
            }
            write_json(workspace / "request.json", request)
            request_digest = digest_file(workspace / "request.json")
            config_digest = identity(configuration)
            if destination.exists() or destination.is_symlink():
                receipt = Receipt.model_validate(read_json(resolve_evidence(destination, "receipt.json")))
                if receipt.request_sha256 != request_digest or receipt.configuration_sha256 != config_digest:
                    raise ValueError("completed coverage request differs; use a new work directory")
                actual = file_manifest(destination)
                actual.pop("receipt.json")
                if actual != receipt.files:
                    raise ValueError("completed coverage evidence changed")
                if Score.model_validate(read_json(destination / "response.json")) != receipt.score:
                    raise ValueError("coverage receipt disagrees with saved response")
            else:
                if cached_only:
                    raise ValueError("completed coverage request is missing")
                inputs = file_manifest(workspace)
                with subprocess.Popen(
                    [*configuration.command, "--request", "request.json", "--output", "response.json"],
                    cwd=workspace,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=os.name == "posix",
                ) as process:
                    try:
                        stdout, stderr = process.communicate(timeout=configuration.timeout_seconds)
                    except BaseException:
                        if os.name == "posix":
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                        stdout, stderr = process.communicate()
                        (workspace / "stdout.log").write_bytes(stdout)
                        (workspace / "stderr.log").write_bytes(stderr)
                        failed = work_root / ("failed-" + workspace.name.removeprefix(".pending-"))
                        workspace.rename(failed)
                        raise ValueError(f"coverage system interrupted or timed out; evidence: {failed}") from None
                if process.returncode:
                    (workspace / "stdout.log").write_bytes(stdout)
                    (workspace / "stderr.log").write_bytes(stderr)
                    failed = work_root / ("failed-" + workspace.name.removeprefix(".pending-"))
                    workspace.rename(failed)
                    raise ValueError(f"coverage system exited {process.returncode}; evidence: {failed}")
                files = file_manifest(workspace)
                score = Score.model_validate(read_json(workspace / "response.json"))
                if any(files.get(name) != digest for name, digest in inputs.items()):
                    raise ValueError("coverage system modified its supplied evidence")
                receipt = Receipt(
                    request_sha256=request_digest,
                    configuration_sha256=config_digest,
                    files=files,
                    score=score,
                )
                write_json(workspace / "receipt.json", receipt)
                # Same-filesystem rename publishes the complete request and response together.
                workspace.rename(destination)
            scores[subset.eval_subset_id] = receipt.score
            receipts[subset.eval_subset_id] = identity(receipt)
    return CoverageScores(
        context=ctx,
        configuration=configuration,
        case_description_sha256=digest_bytes(json_bytes(case_description)),
        scores=scores,
        receipts=receipts,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("results", "subsets", "selection", "config", "case-description", "record-root", "work-root", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)

    parser.add_argument("--cached-only", action="store_true")

    def run(args: argparse.Namespace) -> None:
        description = read_json(args.case_description)
        if not isinstance(description, dict):
            raise ValueError("case description must be a JSON object")
        write_json(
            args.output,
            invoke_coverage(
                RawResults.model_validate(read_json(args.results)),
                Subsets.model_validate(read_json(args.subsets)),
                ScoringConfig.model_validate(read_json(args.selection)),
                CoverageConfig.model_validate(read_json(args.config)),
                description,
                args.record_root,
                args.work_root,
                cached_only=args.cached_only,
            ),
        )

    cli(parser, run)
