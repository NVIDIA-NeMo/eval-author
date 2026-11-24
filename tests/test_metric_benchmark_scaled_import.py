# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import io
import tarfile
from pathlib import Path

import pytest
from metric_benchmark.models import Evaluation, Execution, RawResults, Score, Task
from metric_benchmark.scaled_import import AgentBinding, TaskBinding, archive_files, verify_observation


def observation():
    digest = "sha256:" + "a" * 64
    task = TaskBinding(
        task_id="task_remote",
        revision=1,
        image_digest=digest,
        pack_sha256=digest,
        pack_path="pack.tar.gz",
        source_sha256=digest,
        profile_id="cfg_member",
        profile_sha256=digest,
    )
    agent = AgentBinding(
        bundle_id="ab_agent",
        run_id="bmr_run",
        fingerprint=digest,
        image_digest="repo@" + digest,
        source_lock_digest=digest,
        context_sha256=digest,
        context_path="context.tar.gz",
    )
    row = Execution(
        agent_run_id="bmr_run",
        agent_bundle_id="local-agent",
        evaluation=Evaluation(
            evaluation_id="ev_test/repetition-000001", execution=2, task=Task(task_id="local-task", revision=1)
        ),
        status="succeeded",
        metrics={"reward": Score(value=0.0)},
        native_results_path="trial",
        native_results_format="directory",
        identity_scheme="scaled-evals-atom-v1",
        identity_proof_path="proof.json",
    )
    metadata = {
        "id": "ev_test",
        "benchmark_run_id": "bmr_run",
        "task_id": "task_remote",
        "task_revision": 1,
        "current_execution": 2,
        "framework_profile_id": "cfg_member",
        "status": "succeeded",
    }
    provenance = {
        "schema_version": "scaled-evals-run-provenance-v2",
        "evaluation_id": "ev_test",
        "status": "succeeded",
        "task": {"id": "task_remote", "revision": 1, "image_digest": digest, "tarball_sha256": "a" * 64},
        "config": {
            "framework_version": "0.25.0",
            "framework_profile_id": "cfg_member",
            "config_hashes": {"framework_config": digest},
            "agent_bundle": {
                "bundle_id": "ab_agent",
                "image_digest": "repo@" + digest,
                "fingerprint": digest,
                "source_lock_digest": digest,
            },
        },
        "runtime": {"handle": "ev_test-x2"},
    }
    return row, task, agent, metadata, provenance


def test_exact_attempt_and_per_member_profile_are_verified():
    verify_observation(*observation())


@pytest.mark.parametrize(
    "section,field,value",
    [
        ("runtime", "handle", "ev_test-x1"),
        ("task", "id", "task_other"),
        ("task", "revision", 2),
        ("task", "tarball_sha256", "b" * 64),
        ("config", "framework_profile_id", "cfg_default"),
        ("config", "framework_version", "0.24.0"),
    ],
)
def test_rejects_wrong_source_profile_framework_and_stale_attempt(section, field, value):
    row, task, agent, metadata, provenance = observation()
    provenance[section][field] = value
    with pytest.raises(ValueError):
        verify_observation(row, task, agent, metadata, provenance)


def test_remote_backend_cannot_be_relabelled_local_or_helix():
    row, *_ = observation()
    payload = {
        "backend": "scaled_evals",
        "run_id": "run",
        "task_inventory": [row.evaluation.task.model_dump()],
        "execution_results": [row.model_dump()],
    }
    RawResults.model_validate(payload)
    for backend in ["local", "helix"]:
        changed = copy.deepcopy(payload)
        changed["backend"] = backend
        with pytest.raises(ValueError):
            RawResults.model_validate(changed)


@pytest.mark.parametrize("name,kind", [("../escape", "file"), ("/absolute", "file"), ("safe", "symlink")])
def test_archive_rejects_traversal_and_links(tmp_path: Path, name: str, kind: str):
    path = tmp_path / "archive.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo(name)
        if kind == "symlink":
            member.type = tarfile.SYMTYPE
            member.linkname = "/outside"
        else:
            member.size = 1
        archive.addfile(member, io.BytesIO(b"x") if kind == "file" else None)
    with pytest.raises(ValueError):
        archive_files(path)


def test_missing_success_evidence_is_unavailable_even_with_a_reward():
    from metric_benchmark.scaled_import import missing_success_evidence

    assert "agent/source-identity.json" in missing_success_evidence(True, {"result.json": "digest"})
    assert missing_success_evidence(False, {"result.json": "digest"}) == []
    assert (
        missing_success_evidence(
            True,
            dict.fromkeys(
                [
                    "agent/source-identity.json",
                    "agent/bundle-identity.json",
                    "agent/model-context.json",
                    "agent/trajectory.json",
                ],
                "digest",
            ),
        )
        == []
    )


def test_standalone_repair_requires_distinct_group_and_submission_receipts(tmp_path):
    from metric_benchmark.config import PinnedInput
    from metric_benchmark.io import digest_file, write_json
    from metric_benchmark.scaled_import import Bindings, Proof, RepairEvidence
    from metric_benchmark.scaled_repair import verify_repair_selection

    row, task, agent, metadata, _ = observation()
    row.metrics = {"reward": Score(value=None, missing_score_reason="missing")}
    row.status = "missing"
    proof = Proof(
        binding_path="bindings.json",
        metadata_path="metadata.json",
        metadata_sha256="sha256:" + "a" * 64,
        native_files={},
    )
    write_json(
        tmp_path / "metadata.json",
        {**metadata, "credentials": {}, "network_policy": None, "network_policy_config": None, "parallelism": 1},
    )
    proof.metadata_sha256 = digest_file(tmp_path / "metadata.json")
    write_json(tmp_path / "proof.json", proof)
    write_json(
        tmp_path / "source.json",
        RawResults(backend="scaled_evals", run_id="old", task_inventory=[row.evaluation.task], execution_results=[row]),
    )
    agent.original_run_id = agent.run_id
    agent.run_id = "derived-run"
    bindings = Bindings(
        case_id="case",
        benchmark_id="b",
        benchmark_revision=1,
        tasks={"local-task": task},
        agents={"local-agent": agent},
        repair_source=PinnedInput(path="source.json", sha256=digest_file(tmp_path / "source.json")),
    )
    row.agent_run_id = agent.run_id
    row.identity_scheme = "scaled-evals-atom-repair-v1"
    row.identity_proof_path = "derived-proof.json"
    assert verify_repair_selection(row, proof, bindings, tmp_path, metadata, {}) == "bmr_run"
    request = {
        "task_id": task.task_id,
        "task_revision": 1,
        "framework_profile_id": task.profile_id,
        "framework": "harbor",
        "framework_version": "0.25.0",
        "runtime": "sandbox_k8s",
        "n_attempts": 1,
        "agent_bundle_id": agent.bundle_id,
        "name": "repair",
        "credentials": {},
        "network_policy": None,
        "network_policy_config": None,
        "parallelism": 1,
    }
    new = {**request, "id": "ev_new", "benchmark_run_id": None}
    write_json(tmp_path / "request.json", {"evaluation_id": "ev_test/repetition-000001", "request": request})
    write_json(tmp_path / "created.json", new)
    proof.repair = RepairEvidence(
        original_evaluation_id="ev_test/repetition-000001",
        request=PinnedInput(path="request.json", sha256=digest_file(tmp_path / "request.json")),
        creation=PinnedInput(path="created.json", sha256=digest_file(tmp_path / "created.json")),
    )
    row.evaluation.evaluation_id = "ev_new/repetition-000001"
    assert verify_repair_selection(row, proof, bindings, tmp_path, new, {}) is None
    with pytest.raises(ValueError, match="pinned execution"):
        verify_repair_selection(row, proof, bindings, tmp_path, {**new, "framework_version": "0.24.0"}, {})
    with pytest.raises(ValueError, match="original credentials"):
        verify_repair_selection(row, proof, bindings, tmp_path, {**new, "credentials": {"openai": "different"}}, {})
    from metric_benchmark.io import read_object

    original = read_object(tmp_path / "source.json")
    original["execution_results"][0]["metrics"]["reward"] = {"value": 0.0}
    (tmp_path / "source.json").unlink()
    write_json(tmp_path / "source.json", original)
    assert bindings.repair_source is not None
    bindings.repair_source.sha256 = digest_file(tmp_path / "source.json")
    with pytest.raises(ValueError, match="available original"):
        verify_repair_selection(row, proof, bindings, tmp_path, new, {})


def test_native_extraction_requires_unique_trial_and_provenance(tmp_path):
    from metric_benchmark.scaled_import import extract_native_trial
    from test_metric_benchmark_validation import result_example

    _, _, raw, root, _ = result_example(tmp_path / "fixture")
    result = (root / raw.execution_results[0].native_results_path / "result.json").read_bytes()
    files = {"job/trial/result.json": result, "job/trial/agent/trace.json": b"{}"}
    assert extract_native_trial(files, tmp_path / "output") is None
    files["artifacts/scaled-evals-provenance.json"] = b"{}"
    extracted = extract_native_trial(files, tmp_path / "output")
    assert extracted is not None
    trial, native = extracted
    assert native.name == trial.trial_name
    assert (native / "result.json").read_bytes() == result
    assert (native / "agent/trace.json").read_bytes() == b"{}"
    files["another/trial/result.json"] = result
    with pytest.raises(ValueError, match="ambiguous"):
        extract_native_trial(files, tmp_path / "ambiguous")


def test_extracts_five_distinct_samples_and_marks_absent_tail(tmp_path):
    import json

    from metric_benchmark.scaled_import import extract_native_trial
    from test_metric_benchmark_validation import result_example

    _, _, raw, root, _ = result_example(tmp_path / "fixture")
    original = json.loads((root / raw.execution_results[0].native_results_path / "result.json").read_text())
    files = {"artifacts/scaled-evals-provenance.json": b"{}"}
    for index in range(1, 6):
        native = {**original, "trial_name": f"sample-{index}", "verifier_result": {"rewards": {"reward": index / 5}}}
        files[f"job/sample-{index}/result.json"] = json.dumps(native).encode()
    for index in range(1, 6):
        extracted = extract_native_trial(files, tmp_path / "output", repetition=index, repetitions=5)
        assert extracted is not None
        trial, directory = extracted
        assert trial.trial_name == directory.name == f"sample-{index}"
        assert trial.verifier_result is not None
        assert trial.verifier_result.rewards is not None
        assert trial.verifier_result.rewards["reward"] == index / 5
    del files["job/sample-5/result.json"]
    assert extract_native_trial(files, tmp_path / "missing", repetition=5, repetitions=5) is None
    with pytest.raises(ValueError, match="more native trials"):
        extract_native_trial(files, tmp_path / "excess", repetitions=1)


def test_repeated_evaluation_checks_budget_and_uses_each_native_outcome(tmp_path):
    from harbor.models.trial.result import TrialResult
    from metric_benchmark.io import read_object
    from metric_benchmark.scaled_import import scaled_measurements
    from test_metric_benchmark_validation import result_example

    row, task, agent, metadata, provenance = observation()
    row.evaluation.repetition = 3
    row.evaluation.evaluation_id = "ev_test/repetition-000003"
    metadata["n_attempts"] = 5
    provenance["execution_inputs"] = {"evaluation": {"n_attempts": 5}}
    verify_observation(row, task, agent, metadata, provenance)
    provenance["execution_inputs"]["evaluation"]["n_attempts"] = 1
    with pytest.raises(ValueError, match="framework/profile"):
        verify_observation(row, task, agent, metadata, provenance)
    _, _, raw, root, _ = result_example(tmp_path / "fixture")
    trial = TrialResult.model_validate(read_object(root / raw.execution_results[0].native_results_path / "result.json"))
    status, metrics = scaled_measurements(trial, {"status": "failed", "n_attempts": 5})
    assert status == "succeeded" and metrics["reward"].value == 1
    with pytest.raises(ValueError, match="not terminal"):
        scaled_measurements(trial, {"status": "running", "n_attempts": 5})


def test_standalone_preflight_does_not_claim_benchmark_membership():
    row, task, agent, metadata, provenance = observation()
    agent.run_id = row.agent_run_id = metadata["id"]
    metadata["benchmark_run_id"] = None
    verify_observation(row, task, agent, metadata, provenance)
    metadata["benchmark_run_id"] = "unrelated-run"
    with pytest.raises(ValueError, match="identity differs"):
        verify_observation(row, task, agent, metadata, provenance)


def test_missing_publication_digest_requires_explicit_unavailable_binding():
    row, task, agent, metadata, provenance = observation()
    del provenance["task"]["tarball_sha256"]
    with pytest.raises(ValueError, match="publication differs"):
        verify_observation(row, task, agent, metadata, provenance)
    task.provenance_pack_digest = "unavailable"
    verify_observation(row, task, agent, metadata, provenance)
    provenance["task"]["tarball_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="publication differs"):
        verify_observation(row, task, agent, metadata, provenance)


@pytest.mark.parametrize("prefix", ["artifacts", "ev_test"])
def test_results_and_harbor_viewer_exports_retain_exact_provenance(prefix):
    from metric_benchmark.scaled_import import archive_provenance

    content = b'{"evaluation_id":"ev_test","status":"succeeded"}'
    files = {prefix + "/scaled-evals-provenance.json": content}
    assert archive_provenance(files) == {"evaluation_id": "ev_test", "status": "succeeded"}
    files["another/scaled-evals-provenance.json"] = content
    with pytest.raises(ValueError, match="ambiguous"):
        archive_provenance(files)


def test_repair_groups_share_one_replacement_only_within_original_evaluation():
    from metric_benchmark.scaled_repair import validate_repair_groups

    original_ids = {"old/rep1": "old", "old/rep2": "old", "other/rep1": "other"}
    state = {"old/rep1": {"id": "new"}, "old/rep2": {"id": "new"}}
    validate_repair_groups(state, original_ids)
    with pytest.raises(ValueError, match="only one replacement"):
        validate_repair_groups({**state, "old/rep2": {"id": "another"}}, original_ids)
    with pytest.raises(ValueError, match="different original"):
        validate_repair_groups({**state, "other/rep1": {"id": "new"}}, original_ids)
