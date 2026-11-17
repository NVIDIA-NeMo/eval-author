# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def native_module():
    sys.path[:0] = [str(ROOT), str(ROOT / "skills/eval-author-rubric/scripts")]
    name = "skills.eval-author-rubric.scripts.rubric_example_native"
    importlib.import_module("skills.eval-author-rubric.scripts.measure")
    return importlib.import_module(name)


def test_current_collector_admission_requires_external_registry(tmp_path, monkeypatch):
    module = native_module()
    collector = importlib.import_module("rubric_native_collect")
    monkeypatch.setenv("EVAL_AUTHOR_NATIVE_REGISTRY", str(tmp_path / "registry"))
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "native-index.json").write_text(json.dumps({"schema": collector.SCHEMA, "files": []}))
    with pytest.raises(ValueError, match="native"):
        module.select_bundle(tmp_path, "bundle")
    assert not (tmp_path / "registry").exists()


def test_current_collector_binds_complete_sources_and_registry(tmp_path, monkeypatch):
    native_module()
    collector = importlib.import_module("rubric_native_collect")
    monkeypatch.setenv("EVAL_AUTHOR_NATIVE_REGISTRY", str(tmp_path / "registry"))
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    document = {
        "schema": collector.SCHEMA,
        "producer": collector.producer_identity(),
        "bundle": str(bundle),
        "files": [],
    }
    raw = json.dumps(document).encode()
    collector.register(raw, bundle)
    assert collector.admission(raw, bundle) == document
    changed = {**document, "files": [{"path": "fake", "sha256": "0" * 64}]}
    with pytest.raises(ValueError, match="native"):
        collector.admission(json.dumps(changed).encode(), bundle)
    monkeypatch.setattr(collector, "producer_identity", lambda: {"changed": True})
    with pytest.raises(ValueError, match="native"):
        collector.admission(raw, bundle)


def test_collection_accounts_for_both_failed_native_attempts(tmp_path, monkeypatch):
    native_module()
    collector = importlib.import_module("rubric_native_collect")
    monkeypatch.setenv("EVAL_AUTHOR_NATIVE_REGISTRY", str(tmp_path / "registry"))
    monkeypatch.setattr(collector, "preflight", lambda *args: None)
    import subprocess

    def failed(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1)

    monkeypatch.setattr(collector.recorder.subprocess, "run", failed)
    output = tmp_path / "pair"
    result = collector.collect(output, None)
    assert result["counts"] == {"attempts": 2, "completed": 0, "infrastructure_errors": 2}
    assert result["full_task_acceptance"] is False
    assert len(list((output / "manifest.json.attempts").glob("*.json"))) == 2
    assert len(list((output / "receipts").glob("*.json"))) == 2
    assert collector.admission((output / "native-index.json").read_bytes(), output)
    import shutil

    moved = tmp_path / "moved"
    shutil.copytree(output, moved, symlinks=True)
    document = collector.admission((moved / "native-index.json").read_bytes(), moved)
    assert native_module().validate_pair(moved, document)["counts"] == result["counts"]
    (moved / "receipts/nop.json").unlink()
    with pytest.raises(ValueError, match="native"):
        native_module().validate_pair(moved, document)


def test_pair_projection_keeps_missing_scores_unassessed():
    module = native_module()
    assessment = importlib.import_module("rubric_example_assessment")
    rows = {}
    assessment._ensure_rows(rows)
    module.apply(
        rows,
        {
            "scope": "eval-author.refund-native-pair.v1",
            "index_digest": "a" * 64,
            "counts": {"attempts": 2, "completed": 2, "infrastructure_errors": 0},
            "observations": {},
        },
    )
    assert all(row["score"] is None for row in rows.values())
    check = next(c for c in rows["provenance.runtime-route"]["check_results"] if c["id"].endswith("deterministic-2"))
    assert check["outcome"] == "PARTIAL"


def test_private_environment_does_not_forward_credentials(tmp_path, monkeypatch):
    native_module()
    collector = importlib.import_module("rubric_native_collect")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-do-not-forward")
    monkeypatch.setenv("DOCKER_HOST", "tcp://foreign:2375")
    monkeypatch.setenv("PYTHONPATH", "/foreign")
    env = collector.private_environment(tmp_path, None)
    assert set(env) == {"HOME", "PATH", "TMPDIR", "PYTHONDONTWRITEBYTECODE"}
    assert env["HOME"] == str(tmp_path / "home")


@pytest.mark.parametrize("mutation", ["reward", "checks", "extra-attempt", "symlink", "source", "resealed-index"])
def test_current_pair_rejects_changed_raw_evidence(tmp_path, monkeypatch, mutation):
    import os
    import shutil

    location = os.environ.get("EVAL_AUTHOR_TEST_NATIVE_PAIR")
    if not location:
        pytest.skip("set EVAL_AUTHOR_TEST_NATIVE_PAIR to a current locally collected pair")
    module = native_module()
    collector = importlib.import_module("rubric_native_collect")
    bundle = tmp_path / "pair"
    shutil.copytree(location, bundle, symlinks=True)
    raw = (bundle / "native-index.json").read_bytes()
    document = collector.admission(raw, bundle)
    # CLI spelling is part of the registered command; do not start a process.
    recorded = json.loads((bundle / "receipts/oracle.json").read_text())["command"][0]
    monkeypatch.setattr(collector.sys, "executable", recorded)
    assert module.validate_pair(bundle, document)["counts"]["completed"] == 2
    if mutation == "reward":
        (bundle / "trials/oracle/verifier/reward.txt").write_text("0\n")
    elif mutation == "checks":
        path = bundle / "trials/oracle/verifier/checks.json"
        data = json.loads(path.read_text())
        data["total_credit"] = 0
        path.write_text(json.dumps(data))
    elif mutation == "extra-attempt":
        (bundle / "manifest.json.attempts" / ("a" * 64 + ".json")).write_text("{}")
    elif mutation == "symlink":
        path = bundle / "trials/oracle/verifier/reward.txt"
        path.unlink()
        path.symlink_to(Path(location) / "trials/oracle/verifier/reward.txt")
    elif mutation == "source":
        path = bundle / "task/tests/verify.py"
        path.write_text(path.read_text() + "\nraise RuntimeError('must never execute retained code')\n")
    else:
        document["files"] = []
        with pytest.raises(ValueError, match="native"):
            collector.admission(json.dumps(document).encode(), bundle)
        return
    with pytest.raises(ValueError, match="native"):
        module.validate_pair(bundle, document)


def test_provider_manifest_controls_use_harbor_validation():
    native_module()
    collector = importlib.import_module("rubric_native_collect")
    result = collector.provider_validation(collector.BASE)
    assert result["provider"] == "harbor"
    assert result["version"] == "0.24.0"
    assert result["valid"]["status"] == "accepted"
    assert result["invalid"]["status"] == "validation_failure"
    assert result["invalid"]["errors"]
    assert result["independence"] is False


def test_observation_contract_maps_real_components_without_owner_approval():
    native_module()
    collector = importlib.import_module("rubric_native_collect")
    packet = collector.observation_contract()
    assert set(packet["actions"]) == {"read", "approve", "refund", "ledger", "complete", "error", "reset"}
    assert all(row["status"] == "real" for row in packet["components"].values())
    assert packet["substitutions"] == []
    assert packet["owner_decisions"] == []
    assert packet["state"]["relation"] == {"nop": 0, "actions": 1, "reset": 0}
    assert "money" in packet["scope"]


def test_environment_projection_does_not_promote_failed_check():
    module = native_module()
    assessment = importlib.import_module("rubric_example_assessment")
    rows = {}
    assessment._ensure_rows(rows)
    row = rows["provenance.runtime-route"]
    row["check_results"][0]["outcome"] = "FAIL"
    module.apply(
        rows,
        {
            "scope": "eval-author.refund-runtime-observation.v1",
            "index_digest": "a" * 64,
            "environment": {"owner_review": "pending"},
        },
    )
    assert row["score"] is None
    assert row["check_results"][0]["outcome"] == "FAIL"


def test_environment_projection_keeps_human_gates_pending():
    module = native_module()
    assessment = importlib.import_module("rubric_example_assessment")
    rows = {}
    assessment._ensure_rows(rows)
    module.apply(
        rows,
        {
            "scope": "eval-author.refund-runtime-observation.v1",
            "index_digest": "a" * 64,
            "environment": {"owner_review": "pending"},
        },
    )
    for ident in ("observed-configuration", "runtime-route", "dependency-substitution"):
        assert rows["provenance." + ident]["score"] == 2
    for ident in ("solver-tool-access", "external-state"):
        row = rows["provenance." + ident]
        assert row["score"] is None
        assert all(c["outcome"] == "PASS" for c in row["check_results"] if c["method"] != "human_review")
        assert next(c for c in row["check_results"] if c["method"] == "human_review")["outcome"] == "NOT_RUN"


@pytest.mark.parametrize("mutation", ["none", "effective", "actions", "reset", "provider", "component"])
def test_environment_observation_real_scores_and_semantic_rejection(tmp_path, mutation):
    import os
    import shutil

    location = os.environ.get("EVAL_AUTHOR_TEST_RUNTIME_OBSERVATION")
    if not location:
        pytest.skip("set EVAL_AUTHOR_TEST_RUNTIME_OBSERVATION to a registered current observation")
    module = native_module()
    collector = importlib.import_module("rubric_native_collect")
    table = importlib.import_module("rubric_table")
    measure = importlib.import_module("measure").measure
    bundle = tmp_path / "environment"
    shutil.copytree(location, bundle, symlinks=True)
    selected = module.select_bundle(tmp_path, "environment")
    selection = table.select_task(tmp_path, "environment/task/task.toml")
    selection["artifacts"].extend(selected["artifacts"])
    selection["example_assessment"]["native_bundle_id"] = selected["native_bundle_id"]
    result = measure(tmp_path, selection)
    for name in ("observed-configuration", "runtime-route", "dependency-substitution"):
        assert result["items"]["provenance." + name]["score"] == 2
    for name in ("solver-tool-access", "external-state"):
        row = result["items"]["provenance." + name]
        assert row["score"] is None
        assert all(c["outcome"] == "PASS" for c in row["check_results"] if c["method"] != "human_review")
    if mutation == "none":
        return
    document = collector.admission((bundle / "native-index.json").read_bytes(), bundle)
    if mutation == "provider":
        document["provider_validation"]["invalid"]["status"] = "accepted"
    elif mutation == "component":
        document["contract"]["components"]["socket-service"]["status"] = "approved_mock"
    else:
        case = "reset" if mutation == "reset" else "actions"
        path = bundle / "trials" / case / "verifier/runtime-observation.json"
        data = json.loads(path.read_text())
        if mutation == "effective":
            data["effective"]["environment.cpus"] = 8
        elif mutation == "actions":
            data["actions"]["errors"][0] = {"ok": True}
        else:
            data["actions"]["before"]["value"]["candidate"]["success"] = True
        path.write_text(json.dumps(data))
        document["files"] = [
            {**r, "path": n + "/" + r["path"]} for n in ("task", "trials") for r in collector.inventory(bundle / n)
        ]
    with pytest.raises(ValueError, match="native"):
        module.validate_observation(bundle, document)


def test_runtime_observation_command_is_separate_from_pair():
    native_module()
    collector = importlib.import_module("rubric_native_collect")
    command = collector.observation_command(Path("/private/observation"))
    assert "rubric_native_collect:RuntimeObserver" in command
    assert "--delete" in command
    assert "nop" in command
    assert collector.native_command(Path("/private/pair"), "nop") != command


def test_observation_validation_rejects_invented_or_stale_inputs(tmp_path):
    module = native_module()
    with pytest.raises(ValueError, match="native"):
        module.validate_observation(tmp_path, {"schema": "invented"})


@pytest.mark.parametrize("mutation", ["none", "source", "origin", "flags", "permission", "probe-kind", "missing-index"])
def test_registered_observation_through_real_consumer(tmp_path, mutation):
    import os
    import shutil

    location = os.environ.get("EVAL_AUTHOR_TEST_RUNTIME_OBSERVATION")
    if not location:
        pytest.skip("set EVAL_AUTHOR_TEST_RUNTIME_OBSERVATION to a registered current observation")
    module = native_module()
    collector = importlib.import_module("rubric_native_collect")
    table = importlib.import_module("rubric_table")
    measure = importlib.import_module("measure").measure
    bundle = tmp_path / "observation"
    shutil.copytree(location, bundle, symlinks=True)
    selection = table.select_task(tmp_path, "observation/task/task.toml")
    selected = module.select_bundle(tmp_path, "observation")
    selection["artifacts"].extend(selected["artifacts"])
    selection["example_assessment"]["native_bundle_id"] = selected["native_bundle_id"]
    before = measure(tmp_path, selection)
    unresolved_dispatch = any(
        "code-dispatch-review-required" in gap
        for gap in before["measurements"]["example_assessment"]["grading_dependencies"]["unresolved_material"]
    )
    for name in ("grading-path", "grading-dependency-access"):
        assert before["items"]["provenance." + name]["score"] == (1 if unresolved_dispatch else 2)
        conditional = next(
            c for c in before["items"]["provenance." + name]["check_results"] if c["id"].endswith("human-review-1")
        )
        assert conditional["outcome"] == ("NOT_RUN" if unresolved_dispatch else "NOT_APPLICABLE")
    observation = before["measurements"]["example_assessment"]["native"]["observations"]
    assert observation["executed_import_trace"] is False
    if mutation == "none":
        return
    if mutation == "source":
        path = bundle / "task/tests/verify.py"
        path.write_text(path.read_text() + "\n# changed current source\n")
        changed = table.select_task(tmp_path, "observation/task/task.toml")
        changed["artifacts"].extend(selected["artifacts"])
        changed["example_assessment"]["native_bundle_id"] = selected["native_bundle_id"]
        selection = changed
    elif mutation == "missing-index":
        (bundle / "native-index.json").unlink()
    else:
        path = bundle / "trials/nop/verifier/runtime-observation.json"
        data = json.loads(path.read_text())
        if mutation == "origin":
            data["root"]["origins"]["refund_scorer"]["file"] = "/work/refund_scorer.py"
        elif mutation == "flags":
            data["root"]["flags"]["no_site"] = 0
        elif mutation == "permission":
            data["solver"]["access"]["/tests"]["writable"] = True
        else:
            data["executed_import_trace"] = True
        path.write_text(json.dumps(data))
        # Check semantic rejection even with a coherently rebuilt raw inventory.
        document = collector.admission((bundle / "native-index.json").read_bytes(), bundle)
        document["files"] = [
            {**r, "path": n + "/" + r["path"]} for n in ("task", "trials") for r in collector.inventory(bundle / n)
        ]
        with pytest.raises((ValueError, KeyError), match="native|/work"):
            module.validate_observation(bundle, document)
    with pytest.raises(ValueError):
        measure(tmp_path, selection)


def test_summary_is_not_an_admitted_native_bundle(tmp_path):
    module = native_module()
    (tmp_path / "summary.json").write_text(json.dumps({"native_trial_count": 9, "source_unchanged": True}))
    with pytest.raises(ValueError, match="native"):
        module.select_bundle(tmp_path, "summary.json")


NATIVE = Path("/Users/asutermorris/.hermes/cache/scratch/pr211-native-refund-vqggavnk")


def real_selection():
    native_module()
    table = importlib.import_module("skills.eval-author-rubric.scripts.rubric_table")
    root = Path("/Users/asutermorris")
    task = (ROOT / "skills/eval-author-rubric/examples/refund/base/task.toml").relative_to(root).as_posix()
    selection = table.select_task(root, task)
    native = native_module().select_bundle(root, NATIVE.relative_to(root).as_posix())
    selection["artifacts"].extend(native["artifacts"])
    selection["example_assessment"]["native_bundle_id"] = native["native_bundle_id"]
    return root, selection


@pytest.mark.skipif(not NATIVE.is_dir(), reason="private native panel is not installed")
@pytest.mark.parametrize(
    "relative",
    [
        "receipts/oracle.json",
        "trials/boundaries/result.json",
        "trials/boundaries/agent/control-exec.json",
        "trials/oracle/verifier/checks.json",
        "trials/oracle/verifier/reward.txt",
        "controls-manifest.json",
        "controlled-task/controls/solver_probe.py",
        "trials/late-closure/verifier/late-boundary.json",
    ],
)
def test_native_raw_bytes_cannot_change_behind_admitted_index(monkeypatch, relative):
    root, selection = real_selection()
    module = importlib.import_module("rubric_example_native")
    original = module.read_selected

    def changed(root, path, *args):
        raw, reason = original(root, path, *args)
        if root / path == NATIVE / relative and raw is not None:
            return raw + b" ", reason
        return raw, reason

    monkeypatch.setattr(module, "read_selected", changed)
    with pytest.raises(ValueError, match="native"):
        importlib.import_module("skills.eval-author-rubric.scripts.measure").measure(root, selection)


@pytest.mark.skipif(not NATIVE.is_dir(), reason="private native panel is not installed")
def test_current_task_change_with_refreshed_selection_does_not_reuse_native(tmp_path):
    import shutil

    root, selection = real_selection()
    task = tmp_path / "task"
    shutil.copytree(ROOT / "skills/eval-author-rubric/examples/refund/base", task)
    with (task / "tests/verify.py").open("a") as stream:
        stream.write("\n# changed revision\n")
    table = importlib.import_module("skills.eval-author-rubric.scripts.rubric_table")
    changed = table.select_task(root, (task / "task.toml").relative_to(root).as_posix())
    changed["artifacts"].append(selection["artifacts"][-1])
    changed["example_assessment"]["native_bundle_id"] = selection["example_assessment"]["native_bundle_id"]
    with pytest.raises(ValueError, match="native"):
        importlib.import_module("skills.eval-author-rubric.scripts.measure").measure(root, changed)


@pytest.mark.skipif(not NATIVE.is_dir(), reason="private native panel is not installed")
def test_selection_wide_denied_native_path_is_not_bypassed():
    root, selection = real_selection()
    record = dict(selection["artifacts"][-1])
    record.update(
        id="denied-native-result",
        path=(NATIVE / "trials/oracle/result.json").relative_to(root).as_posix(),
        access="denied",
        digest=None,
        evidence=[],
    )
    selection["artifacts"].append(record)
    with pytest.raises(ValueError, match="native"):
        importlib.import_module("skills.eval-author-rubric.scripts.measure").measure(root, selection)


@pytest.mark.skipif(not NATIVE.is_dir(), reason="private native panel is not installed")
def test_real_panel_flows_through_measure_without_promoting_absent_checks():
    module = native_module()
    table = importlib.import_module("skills.eval-author-rubric.scripts.rubric_table")
    measure = importlib.import_module("skills.eval-author-rubric.scripts.measure").measure
    root = Path("/Users/asutermorris")
    task = (ROOT / "skills/eval-author-rubric/examples/refund/base/task.toml").relative_to(root).as_posix()
    selection = table.select_task(root, task)
    before = measure(root, selection)
    native = module.select_bundle(root, NATIVE.relative_to(root).as_posix())
    selection["artifacts"].extend(native["artifacts"])
    selection["example_assessment"]["native_bundle_id"] = native["native_bundle_id"]
    after = measure(root, selection)
    panel = after["measurements"]["example_assessment"]["native"]
    assert panel["counts"] == {"attempts": 10, "completed": 9, "infrastructure_errors": 1}
    assert panel["task_freshness"] == "current"
    assert panel["human_review"] is False and panel["independence"] is False
    fields = {row["field"]: row for row in panel["configuration_comparison"]}
    assert fields["solver.uid"]["declared"] == "65534"
    assert fields["solver.uid"]["observed"] == 65534
    assert fields["solver.uid"]["status"] == "match"
    assert fields["agent.timeout_sec"]["observed"] is None
    assert fields["agent.timeout_sec"]["status"] == "unknown"
    assert fields["environment.network_mode"]["status"] == "partial"
    assert fields["environment.network_mode"]["observed"] == "egress_controller_enabled"
    assert all(row["declared_ref"] for row in fields.values())
    for ident in (
        "AC-01",
        "AC-03",
        "AC-04",
        "provenance.observed-configuration",
        "provenance.runtime-route",
        "provenance.solver-tool-access",
    ):
        assert before["items"][ident]["score"] is None
        assert after["items"][ident]["score"] == 1
        assert after["items"][ident]["evidence_refs"]
    assert (
        next(
            c for c in after["items"]["provenance.solver-tool-access"]["check_results"] if c["method"] == "human_review"
        )["outcome"]
        == "NOT_RUN"
    )
    assert next(c for c in after["items"]["AC-04"]["check_results"] if c["method"] == "probe")["outcome"] == "NOT_RUN"
    assert after["items"]["task-contract.scoring-authority"]["score"] is None
    assert after["items"]["AC-02"] == before["items"]["AC-02"]
    assert next(c for c in after["items"]["AC-03"]["check_results"] if c["method"] == "probe")["outcome"] == "NOT_RUN"
    assert len(json.dumps(after).encode()) < 1_000_000, "native report must fit retained table reader"


def test_projection_preserves_confirmed_defect_and_existing_findings():
    module = native_module()
    assessment = importlib.import_module("rubric_example_assessment")
    rows = {}
    assessment._ensure_rows(rows)
    rows["AC-03"].update(score=0, findings=[{"code": "confirmed-existing-defect"}])
    module.apply(
        rows, {"counts": {}, "observations": {}, "configuration_comparison": [], "controls": [], "evidence_refs": []}
    )
    assert rows["AC-03"]["score"] == 0
    assert rows["AC-03"]["findings"][0]["code"] == "confirmed-existing-defect"


def test_resealed_index_cannot_admit_caller_evidence(tmp_path):
    module = native_module()
    (tmp_path / "panel").mkdir()
    (tmp_path / "panel/final-artifact-hashes.json").write_text(
        json.dumps([{"path": "summary.json", "sha256": "0" * 64}])
    )
    with pytest.raises(ValueError, match="native"):
        module.select_bundle(tmp_path, "panel")


@pytest.mark.skipif(not NATIVE.is_dir(), reason="private native panel is not installed")
def test_extra_registered_attempt_cannot_disappear(monkeypatch):
    root, selection = real_selection()
    original = Path.iterdir

    def extra(path):
        yield from original(path)
        if path == NATIVE / "manifest.json.attempts":
            yield path / ("f" * 64 + ".json")

    monkeypatch.setattr(Path, "iterdir", extra)
    with pytest.raises(ValueError, match="native"):
        importlib.import_module("skills.eval-author-rubric.scripts.measure").measure(root, selection)
