# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author-task-create/scripts"
AUDIT_SKILL = SCRIPTS.parents[1] / "eval-author-audit"
sys.path.insert(0, str(SCRIPTS))
evidence = importlib.import_module("task_evidence")
shared_evidence = importlib.import_module("eval_author_shared.task_evidence")
pipeline = importlib.import_module("task_pipeline")

FAKE_HARBOR = """
import json, sys, uuid
from pathlib import Path

args = sys.argv[1:]
assert args[:2] == ["trial", "start"], args
options = dict(zip(args[2::2], args[3::2]))
trial = Path(options["--trials-dir"]) / options["--trial-name"]
agent = options["-a"]
trial.mkdir(parents=True, exist_ok=True)
if agent not in ("nop", "oracle", "wrong-answer"):
    call = {"tool_call_id": "call-1", "function_name": "customer.lookup", "arguments": {}}
    steps = [
        {"step_id": 1, "source": "user", "message": "Look up the customer."},
        {"step_id": 2, "source": "agent", "message": "Looking up.", "tool_calls": [call]},
    ]
    atif = {"schema_version": "ATIF-v1.7", "session_id": trial.name, "trajectory_id": trial.name,
            "agent": {"name": "example-agent", "version": "1.0.0"}, "steps": steps}
    (trial / "agent").mkdir(exist_ok=True)
    (trial / "agent" / "trajectory.json").write_text(json.dumps(atif))
result = {"id": str(uuid.uuid4()), "task_name": Path(options["-p"]).name, "trial_name": trial.name,
          "config": {"agent": {"name": agent}},
          "task_checksum": "synthetic", "exception_info": None,
          "verifier_result": {"rewards": {"reward": 0 if agent in ("nop", "wrong-answer") else 1}}}
(trial / "result.json").write_text(json.dumps(result))
"""


def write(path, value):
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    # Generic receipt invariants use a synthetic runtime; native NeMo Gym is tested separately.
    import harbor.models.task.task

    monkeypatch.setattr(harbor.models.task.task, "Task", lambda path: SimpleNamespace(checksum="synthetic"))
    monkeypatch.setattr(evidence, "native_identity", lambda native, manifest: native["rollout_id"])
    task = tmp_path / "cover-read"
    task.mkdir()
    (task / "instruction.md").write_text("Read the record and return its total.")
    contract = {
        "task_id": "cover-read",
        "provider": "harbor",
        "native_run_id": "/rollout_id",
        "alternative_not_applicable": "This fixture has a single numeric output.",
        "side_effect_not_applicable": "This task only returns a value.",
        "cases": {},
    }
    for kind, reward in [("reference", 1), ("incorrect", 0), ("empty", 0), ("agent", 1)]:
        contract["cases"][kind] = {
            "kind": kind,
            "requirement": "Return the correct total.",
            "health": [{"pointer": "/error", "equals": None}],
            "checks": [{"pointer": "/reward", "min": reward, "max": reward}],
        }
    manifest = tmp_path / "manifest.json"
    contract_path = write(tmp_path / "contract.json", contract)
    evidence.prepare(task, contract_path, manifest)
    return task, contract_path, manifest


def execute(tmp_path, manifest, kind, run_id, *, reward=1, error=None, missing=False, command=None):
    result = tmp_path / f"{run_id}-native.json"
    receipt = tmp_path / f"{run_id}-receipt.json"
    trace = tmp_path / f"{run_id}-atif.json" if kind == "agent" else None
    payload = {"rollout_id": run_id, "reward": reward, "error": error}
    if missing:
        del payload["reward"]
    code = f"from pathlib import Path; Path({str(result)!r}).write_text({json.dumps(payload)!r})"
    if trace:
        code += f"; Path({str(trace)!r}).write_text({json.dumps({'session_id': run_id})!r})"
    evidence.run(manifest, kind, run_id, receipt, result, trace, command or [sys.executable, "-c", code])
    return receipt


def complete(tmp_path, manifest):
    return [
        execute(tmp_path, manifest, "reference", "reference"),
        execute(tmp_path, manifest, "incorrect", "incorrect", reward=0),
        execute(tmp_path, manifest, "empty", "empty", reward=0),
        execute(tmp_path, manifest, "agent", "repeat-1"),
        execute(tmp_path, manifest, "agent", "repeat-2"),
    ]


def reports(tmp_path):
    before = write(
        tmp_path / "before.json",
        {"uncovered_items": [{"name": "read", "kind": "tool", "reason": "not_covered_by_any_input_report"}]},
    )
    after = []
    for run_id in ("repeat-1", "repeat-2"):
        after.append(
            write(
                tmp_path / f"{run_id}-report.json",
                {
                    "covered": ["read"],
                    "uncovered": [],
                    "input_reports": [
                        {
                            "item_kind": "tool",
                            "covered": ["read"],
                            "subject": {
                                "task_id": "cover-read",
                                "run_id": run_id,
                                "trace": str(tmp_path / f"{run_id}-atif.json"),
                            },
                        }
                    ],
                },
            )
        )
    return before, after


@pytest.mark.parametrize("missing", [False, True])
def test_historical_manifest_checks_produced_receipts_without_current_task_proof(
    tmp_path, prepared, missing, monkeypatch
):
    audit = importlib.import_module("audit_verifier_evidence")
    task, _, manifest_path = prepared
    receipts = complete(tmp_path, manifest_path)
    if missing:
        receipts = [path for path in receipts if path.name != "incorrect-receipt.json"]
    (task / "instruction.md").write_text("Changed after the retained controls ran.")
    review = audit.Review(tmp_path, [tmp_path])
    manifest = review.object(manifest_path)
    rows = [
        audit.inspect_receipt(review, path, review.object(path), {task}, verify_historical=True) for path in receipts
    ]
    default = audit.inspect_manifest(review, manifest_path, manifest, {task}, rows)
    assert default == [
        {
            "path": "manifest.json",
            "task": "cover-read",
            "classification": "stale",
            "reason": "This manifest describes an earlier task revision",
        }
    ]

    def forbidden(*args, **kwargs):
        pytest.fail("Historical manifest validation must not measure the current task")

    monkeypatch.setattr(review, "task_digest", forbidden)
    historical = audit.inspect_manifest(review, manifest_path, manifest, {task}, rows, verify_historical=True)
    assert historical == (
        [
            {
                "path": "manifest.json",
                "task": "cover-read",
                "classification": "unproven",
                "reason": "Declared controls have no retained receipts",
                "cases": ["incorrect"],
            }
        ]
        if missing
        else []
    )
    assert {row["classification"] for row in rows} == {"stale", "agent_run"}
    assert all(row["historical_classification"] == "passed" for row in rows if row["classification"] == "stale")


def test_acceptance_requires_controls_and_exact_agent_evidence(tmp_path, prepared, monkeypatch):
    # Isolate receipt invariants here; the documented flow below uses real bound measurements.
    monkeypatch.setattr(pipeline, "_verify_coverage_binding", lambda path: {"audit_sha256": "fixture"})
    monkeypatch.setattr(pipeline, "_verify_lineage", lambda *args: None)
    _, _, manifest = prepared
    receipts = complete(tmp_path, manifest)
    before, after = reports(tmp_path)
    code, verdict = pipeline._verify(before, after, "read", receipts)
    assert code == 0 and verdict["accepted"] and verdict["coverage_closed"]
    with pytest.raises(pipeline.PipelineError, match="execution receipts"):
        pipeline._verify(before, after, "read")
    with pytest.raises(pipeline.PipelineError, match="every declared control"):
        pipeline._verify(before, after, "read", receipts[1:])
    payload = evidence.read(after[1])
    payload["covered"] = []
    payload["uncovered"] = ["read"]
    payload["input_reports"][0]["covered"] = []
    write(after[1], payload)
    code, verdict = pipeline._verify(before, after, "read", receipts)
    assert code == 1 and not verdict["accepted"] and verdict["task_validation"] == "passed"


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("task", "task revision changed"),
        ("native", "native result digest"),
        ("trace", "trace is changed"),
        ("manifest", "manifest digest"),
        ("wrong_task", "subject.task_id"),
        ("wrong_trace", "subject.trace"),
        ("run_id", "must match all supplied"),
        ("mixed_runs", "exactly one agent run"),
    ],
)
def test_rejects_stale_or_unrelated_evidence(tmp_path, prepared, mutation, message):
    task, _, manifest = prepared
    receipts = complete(tmp_path, manifest)
    before, after = reports(tmp_path)
    if mutation == "task":
        (task / "instruction.md").write_text("Changed task")
    elif mutation == "native":
        (tmp_path / "repeat-1-native.json").write_text("{}")
    elif mutation == "trace":
        (tmp_path / "repeat-1-atif.json").write_text("{}")
    elif mutation == "manifest":
        manifest.write_text(manifest.read_text() + "\n")
    else:
        payload = evidence.read(after[0])
        subject = payload["input_reports"][0]["subject"]
        if mutation == "wrong_task":
            subject["task_id"] = "unrelated"
        elif mutation == "wrong_trace":
            subject["trace"] = str(tmp_path / "repeat-2-atif.json")
        elif mutation == "run_id":
            subject["run_id"] = "unrelated"
        else:
            payload["input_reports"].append({"subject": {**subject, "run_id": "unrelated"}})
        write(after[0], payload)
    with pytest.raises(pipeline.PipelineError, match=message):
        pipeline._verify(before, after, "read", receipts)


@pytest.mark.parametrize(
    "kwargs,status",
    [
        ({"reward": 0}, "behavior_failed"),
        ({"error": "setup failed"}, "infrastructure_error"),
        ({"missing": True}, "infrastructure_error"),
        ({"command": [sys.executable, "-c", "raise SystemExit(2)"]}, "infrastructure_error"),
    ],
)
def test_failed_behavior_is_distinct_from_missing_or_broken_evidence(tmp_path, prepared, kwargs, status):
    receipt = execute(tmp_path, prepared[2], "reference", "bad", **kwargs)
    assert evidence.read(receipt)["status"] == status
    with pytest.raises(evidence.EvidenceError, match="not passed"):
        evidence.verify_receipts([receipt], "cover-read")


@pytest.mark.parametrize(
    "kwargs,status",
    [
        ({"reward": 0}, "passed"),
        ({"reward": 1}, "behavior_failed"),
        ({"reward": 0, "error": "verifier failed"}, "infrastructure_error"),
        ({"missing": True}, "infrastructure_error"),
    ],
)
def test_declared_cheat_case_is_required_and_fail_closed(tmp_path, prepared, kwargs, status):
    # Synthetic native results test bookkeeping, not real provider cheat resistance.
    task, contract_path, _ = prepared
    contract = evidence.read(contract_path)
    contract["cases"]["early-exit"] = {
        **contract["cases"]["incorrect"],
        "requirement": "Successful exit without the required work earns no credit.",
    }
    write(contract_path, contract)
    manifest = tmp_path / "screened-manifest.json"
    evidence.prepare(task, contract_path, manifest)
    receipts = complete(tmp_path, manifest)
    with pytest.raises(evidence.EvidenceError, match="every declared control"):
        evidence.verify_receipts(receipts, "cover-read")
    attack = execute(tmp_path, manifest, "early-exit", "attack-1", **kwargs)
    assert evidence.read(attack)["status"] == status
    if status == "passed":
        evidence.verify_receipts([*receipts, attack], "cover-read")
    else:
        with pytest.raises(evidence.EvidenceError, match="not passed"):
            evidence.verify_receipts([*receipts, attack], "cover-read")


def test_preexisting_results_cannot_be_adopted_as_fresh_execution(tmp_path, prepared):
    (tmp_path / "old-native.json").write_text("{}")
    with pytest.raises(evidence.EvidenceError, match="must be new"):
        execute(tmp_path, prepared[2], "reference", "old")


def test_repair_updates_manifest_in_place_and_requires_fresh_proof(tmp_path, prepared):
    task, contract, manifest = prepared
    receipts = complete(tmp_path, manifest)
    for index in range(3):
        (task / "instruction.md").write_text(f"Clarified requirement {index}")
        reason = f"instruction_ambiguity: clarify visible requirement {index}"
        replaced = evidence.digest(manifest)
        evidence.prepare(task, contract, manifest, manifest, reason)
        current = evidence.manifest_at(manifest)
        assert current["supersedes"] == {"manifest_sha256": replaced}
        assert current["repair_count"] == index + 1
        assert current["repair_reason"] == reason
        assert "repairs" not in current
        assert "manifest" not in current
        assert "history" not in current
    with pytest.raises(evidence.EvidenceError, match="manifest digest mismatch"):
        evidence.verify_receipts(receipts, "cover-read")
    with pytest.raises(evidence.EvidenceError, match="budget exhausted"):
        evidence.prepare(task, contract, manifest, manifest, "another repair")
    assert list(tmp_path.glob("manifest*.json")) == [manifest]


def test_repair_resumes_without_old_manifest_copies(tmp_path, prepared):
    task, contract, previous = prepared
    manifest = tmp_path / "current.json"
    replaced = evidence.digest(previous)
    evidence.prepare(task, contract, manifest, previous, "Fix the expected result")
    assert "supersedes" not in evidence.read(previous)
    assert evidence.read(manifest)["supersedes"] == {"manifest_sha256": replaced}
    previous.unlink()
    assert evidence.manifest_at(manifest)["repair_count"] == 1


def test_fresh_controls_pass_after_in_place_repair_without_removing_native_results(tmp_path, prepared):
    task, contract, manifest = prepared
    original = complete(tmp_path, manifest)
    evidence.prepare(task, contract, manifest, manifest, "Correct a grading requirement")
    assert all(path.is_file() for path in original)
    with pytest.raises(evidence.EvidenceError, match="manifest digest mismatch"):
        evidence.verify_receipts(original, "cover-read")
    current = tmp_path / "current"
    current.mkdir()
    fresh = complete(current, manifest)
    assert len(evidence.verify_receipts(fresh, "cover-read")) == 2
    assert all(path.is_file() for path in original)


TRIALS = [
    ("reference", "reference", "oracle", 1),
    ("incorrect", "incorrect", "wrong-answer", 0),
    ("empty", "empty", "nop", 0),
    ("agent", "repeat-1", "example-agent", 1),
    ("agent", "repeat-2", "example-agent", 1),
]


def harbor_trial(tmp_path, manifest, case, run_id, agent, reward, regrade_of=None):
    """Record a synthetic trial or regrade with Harbor's result fields."""
    result = tmp_path / f"{run_id}-result.json"
    receipt = tmp_path / f"{run_id}-receipt.json"
    trace = tmp_path / f"{run_id}-trajectory.json" if case == "agent" else None
    native = {"id": f"{run_id}-trial", "rollout_id": run_id, "reward": reward, "error": None}
    native["config"] = {"agent": {"name": agent}}
    trajectory = json.dumps({"session_id": run_id})
    if regrade_of is not None:
        source = evidence.read(regrade_of)
        native["config"]["source_trial"] = {
            "action": "regrade",
            "trial_id": evidence.read(Path(source["result"]))["id"],
        }
        if trace:
            trajectory = Path(source["trace"]).read_text()
    code = f"from pathlib import Path; Path({str(result)!r}).write_text({json.dumps(native)!r})"
    if trace:
        code += f"; Path({str(trace)!r}).write_text({trajectory!r})"
    evidence.run(manifest, case, run_id, receipt, result, trace, [sys.executable, "-c", code], regrade_of=regrade_of)
    return receipt


def revise_after_trials(tmp_path, prepared, change):
    task, contract, manifest = prepared
    sources = [harbor_trial(tmp_path, manifest, *trial) for trial in TRIALS]
    (task / "tests").mkdir()
    (task / "tests/test.sh").write_text("#!/bin/sh\nexit 0\n")
    if change == "outside_tests":
        (task / "instruction.md").write_text("Read the record and return its rounded total.")
    evidence.prepare(task, contract, manifest, manifest, "verifier_defect: tighten the total check")
    return sources


def regrade_all(tmp_path, manifest, sources):
    return [
        harbor_trial(tmp_path, manifest, case, f"{run_id}-regraded", agent, reward, source)
        for (case, run_id, agent, reward), source in zip(TRIALS, sources)
    ]


def test_tests_only_repair_regrades_recorded_trials(tmp_path, prepared):
    manifest = prepared[2]
    sources = revise_after_trials(tmp_path, prepared, "tests")
    regraded = regrade_all(tmp_path, manifest, sources)

    assert len(evidence.verify_receipts(regraded, "cover-read")) == 2
    current = evidence.manifest_at(manifest)
    assert current["repair_count"] == 1
    for path, source in zip(regraded, sources):
        record = evidence.read(path)["regrade"]
        assert record["source_receipt"] == str(source.resolve())
        assert record["tests_sha256_before"] != record["tests_sha256_after"] == current["tests_sha256"]


def test_regrade_is_refused_when_files_outside_tests_change(tmp_path, prepared):
    sources = revise_after_trials(tmp_path, prepared, "outside_tests")
    with pytest.raises(evidence.EvidenceError, match="outside tests/"):
        harbor_trial(tmp_path, prepared[2], "reference", "reference-regraded", "oracle", 1, sources[0])


@pytest.mark.parametrize("mutation", ["reuse", "tamper"])
def test_regraded_receipt_binds_one_unchanged_source(tmp_path, prepared, mutation):
    manifest = prepared[2]
    sources = revise_after_trials(tmp_path, prepared, "tests")
    regraded = regrade_all(tmp_path, manifest, sources)
    if mutation == "reuse":
        regraded.append(harbor_trial(tmp_path, manifest, "reference", "reference-again", "oracle", 1, sources[0]))
    else:
        write(sources[0], {**evidence.read(sources[0]), "cwd": str(tmp_path / "elsewhere")})
    with pytest.raises(evidence.EvidenceError, match="regrade record changed"):
        evidence.verify_receipts(regraded, "cover-read")


@pytest.mark.parametrize("field", ["task_without_tests_sha256", "tests_sha256"])
def test_manifest_rechecks_split_digests(tmp_path, prepared, field):
    manifest = prepared[2]
    value = evidence.read(manifest)
    value[field] = "0" * 64
    write(manifest, value)
    with pytest.raises(evidence.EvidenceError, match="tests/ digests differ"):
        evidence.manifest_at(manifest)


@pytest.mark.parametrize("change", ["content", "mode", "directory", "symlink"])
def test_task_digest_split_preserves_full_tree_safeguards(tmp_path, change):
    task = tmp_path / "task"
    (task / "tests").mkdir(parents=True)
    instruction = task / "instruction.md"
    instruction.write_text("Return the total.\n")
    verifier = task / "tests/test.sh"
    verifier.write_text("exit 0\n")
    before = shared_evidence._task_digests(task)
    assert evidence.task_digest(task) == before["task_sha256"]
    if change == "content":
        verifier.write_text("exit 1\n")
    elif change == "mode":
        instruction.chmod(instruction.stat().st_mode ^ 0o100)
    elif change == "directory":
        (task / "data").mkdir()
    else:
        (task / "tests/link").symlink_to(instruction)
        with pytest.raises(evidence.EvidenceError, match="symlinks"):
            shared_evidence._task_digests(task)
        return
    after = shared_evidence._task_digests(task)
    assert after["task_sha256"] != before["task_sha256"]
    changed, unchanged = (
        ("tests_sha256", "task_without_tests_sha256")
        if change == "content"
        else ("task_without_tests_sha256", "tests_sha256")
    )
    assert after[changed] != before[changed]
    assert after[unchanged] == before[unchanged]


def test_regrade_rechecks_source_after_execution(tmp_path, prepared, monkeypatch):
    sources = revise_after_trials(tmp_path, prepared, "tests")
    original_run = subprocess.run

    def change_source(*args, **kwargs):
        completed = original_run(*args, **kwargs)
        write(sources[0], {**evidence.read(sources[0]), "cwd": "changed"})
        return completed

    monkeypatch.setattr(evidence.subprocess, "run", change_source)
    path = harbor_trial(tmp_path, prepared[2], "reference", "regraded", "oracle", 1, sources[0])
    receipt = evidence.read(path)
    assert receipt["status"] == "infrastructure_error"
    assert receipt["error"] == "regrade source changed during execution"


def test_regrade_metadata_cannot_be_omitted_on_recheck(tmp_path, prepared):
    sources = revise_after_trials(tmp_path, prepared, "tests")
    receipts = regrade_all(tmp_path, prepared[2], sources)
    value = evidence.read(receipts[0])
    del value["regrade"]
    write(receipts[0], value)
    with pytest.raises(evidence.EvidenceError, match="require a recorded regrade source"):
        evidence.verify_receipts(receipts, "cover-read")


@pytest.mark.parametrize("mutation", ["health", "status", "identity"])
def test_regrade_source_rechecks_native_health_and_status(tmp_path, prepared, mutation):
    sources = revise_after_trials(tmp_path, prepared, "tests")
    source = evidence.read(sources[0])
    result = Path(source["result"])
    native = evidence.read(result)
    if mutation == "health":
        native["error"] = "verifier failed"
    elif mutation == "status":
        native["reward"] = 0
    else:
        native["rollout_id"] = "unrelated"
    write(result, native)
    source["result_sha256"] = evidence.digest(result)
    write(sources[0], source)
    with pytest.raises(evidence.EvidenceError):
        harbor_trial(tmp_path, prepared[2], "reference", "regraded", "oracle", 1, sources[0])


@pytest.mark.parametrize("bound", [False, True])
def test_tests_only_repair_can_mix_regrades_and_a_new_control(tmp_path, prepared, bound):
    task, contract_path, manifest = prepared
    contract = evidence.read(contract_path)
    plan_path = None
    if bound:
        plan_path = write(tmp_path / "construction.json", native_construction(contract))
        manifest = tmp_path / "bound.json"
        evidence.prepare(task, contract_path, manifest, construction_plan=plan_path)
    sources = [harbor_trial(tmp_path, manifest, *trial) for trial in TRIALS]
    assert len(evidence.verify_receipts(sources, "cover-read")) == 2
    original_plan = plan_path.read_bytes() if plan_path else None
    contract["cases"]["new-negative"] = {**contract["cases"]["incorrect"]}
    write(contract_path, contract)
    if plan_path:
        write(plan_path, native_construction(contract))
    (task / "tests").mkdir()
    (task / "tests/test.sh").write_text("exit 0\n")
    evidence.prepare(task, contract_path, manifest, manifest, "Add a negative control", construction_plan=plan_path)
    receipts = regrade_all(tmp_path, manifest, sources)
    with pytest.raises(evidence.EvidenceError, match="every declared control"):
        evidence.verify_receipts(receipts, "cover-read")
    receipts.append(harbor_trial(tmp_path, manifest, "new-negative", "new-negative", "wrong-answer", 0))
    assert len(evidence.verify_receipts(receipts, "cover-read")) == 2
    if bound:
        previous = evidence.read(manifest)["previous_revision"]
        assert previous["construction_snapshot"].encode() == original_plan
        previous["construction_snapshot"] += " "
        value = evidence.read(manifest)
        value["previous_revision"] = previous
        write(manifest, value)
        with pytest.raises(evidence.EvidenceError, match="snapshot"):
            shared_evidence._regrade_base(value)


@pytest.mark.parametrize(
    "mutation",
    [
        "case_modified",
        "case_deleted",
        "positive_added",
        "requirements",
        "checks",
        "inputs",
        "probes",
        "prerequisites",
        "starting_state",
        "bindings",
        "review",
        "migration",
        "snapshot",
    ],
)
def test_additive_construction_preserves_existing_semantics(prepared, mutation):
    contract = evidence.read(prepared[1])
    old = native_construction(contract)
    old["probes"] = [{"id": "setup", "command": ["true"], "timeout_seconds": 2}]
    old["prerequisites"] = [{"id": "dependency", "before": "setup", "after": "total"}]
    current = json.loads(json.dumps(old))
    current["cases"].append({**current["cases"][1], "case": "new-negative"})
    if mutation == "case_modified":
        current["cases"][1]["rationale"] = "Changed existing behavior"
    elif mutation == "case_deleted":
        current["cases"].pop(1)
    elif mutation == "positive_added":
        current["cases"][-1]["kind"] = "agent"
    elif mutation == "snapshot":
        pass
    elif isinstance(current[mutation], list):
        current[mutation].append({"changed": True})
    else:
        current[mutation] = {"changed": True}
    raw = json.dumps(old)
    current_raw = json.dumps(current)
    with pytest.raises(shared_evidence.construction.PlanError, match="changed|snapshot"):
        shared_evidence.construction.require_regrade_compatible(
            raw + (" " if mutation == "snapshot" else ""),
            hashlib.sha256(raw.encode()).hexdigest(),
            current_raw,
            hashlib.sha256(current_raw.encode()).hexdigest(),
        )


@pytest.mark.parametrize("field", ["requirements", "checks", "inputs", "probes", "prerequisites"])
@pytest.mark.parametrize("operation", ["modify", "delete"])
def test_additive_construction_rejects_changed_or_deleted_rows(field, operation):
    previous = {"cases": [], field: [{"id": "existing", "setting": "unchanged"}]}
    current = json.loads(json.dumps(previous))
    current["cases"].append({"case": "new-negative", "kind": "negative"})
    if operation == "modify":
        current[field][0]["setting"] = "changed"
    else:
        current[field].clear()
    with pytest.raises(shared_evidence.construction.PlanError, match="changed beyond additive"):
        shared_evidence.construction.require_additive_cases(previous, current)


@pytest.mark.parametrize("mutation", ["case", "deleted", "health", "execution", "positive_added"])
def test_regrade_rejects_changed_result_contract(tmp_path, prepared, mutation):
    task, contract_path, manifest = prepared
    sources = revise_after_trials(tmp_path, prepared, "tests")
    contract = evidence.read(contract_path)
    if mutation == "case":
        contract["cases"]["incorrect"]["requirement"] = "Changed behavior"
    elif mutation == "deleted":
        contract["cases"]["renamed"] = contract["cases"].pop("incorrect")
    elif mutation == "health":
        contract["cases"]["reference"]["health"] = [{"pointer": "/other", "equals": None}]
    elif mutation == "execution":
        contract["native_run_id"] = "/id"
    else:
        contract["cases"]["new-positive"] = contract["cases"]["reference"]
    write(contract_path, contract)
    # Keep the original source revision while preparing the changed contract.
    value = evidence.read(manifest)
    value["contract"] = contract
    write(manifest, value)
    with pytest.raises(evidence.EvidenceError, match="changed beyond additive"):
        harbor_trial(tmp_path, manifest, "reference", "regraded", "oracle", 1, sources[0])


@pytest.mark.parametrize("mutation", ["source_id", "same_id", "agent", "empty_agent"])
def test_regrade_requires_source_identity_and_agent(mutation):
    source = {"id": "source-trial", "config": {"agent": {"name": "oracle"}}}
    native = {
        "id": "regraded-trial",
        "config": {"agent": {"name": "oracle"}, "source_trial": {"action": "regrade", "trial_id": "source-trial"}},
    }
    if mutation == "source_id":
        native["config"]["source_trial"]["trial_id"] = "other-trial"
    elif mutation == "same_id":
        native["id"] = source["id"]
    elif mutation == "agent":
        native["config"]["agent"]["name"] = "nop"
    else:
        native["config"]["agent"] = source["config"]["agent"] = {}
    with pytest.raises(evidence.EvidenceError, match="name its source trial"):
        shared_evidence._require_regrade_of(native, source)


@pytest.fixture
def workspace_source(tmp_path, monkeypatch):
    task = tmp_path / "task"
    (task / "tests").mkdir(parents=True)
    (task / "instruction.md").write_text("Return the total.\n")
    (task / "tests/test.sh").write_text("exit 0\n")
    job = tmp_path / "private/jobs/source"
    result = job / "task__trial/result.json"
    result.parent.mkdir(parents=True)
    plan = write(tmp_path / "private/construction.json", {"cases": []})
    context = {"canonical_path": "private/construction.json", "sha256": evidence.digest(plan), "controls": {}}
    inputs = {
        "schema": shared_evidence._workspace.RUN_INPUT_SCHEMA,
        "arm": "oracle",
        "job_dir": "private/jobs/source",
        "task_tree_sha256": shared_evidence._workspace._task_tree_info(task)["task_tree_sha256"],
        **shared_evidence._tests_split(tmp_path),
        "task_checksum": "b" * 64,
        "negative_control": None,
        "construction_sha256": context["sha256"],
        "control_id": None,
        "construction_path": context["canonical_path"],
        "construction_snapshot": plan.read_text(),
    }
    input_path = shared_evidence._run_input_path(tmp_path, job)
    input_path.parent.mkdir()
    write(input_path, inputs)
    write(
        result,
        {
            "id": "source-trial",
            "config": {"job_id": "source-job", "agent": {"name": "oracle"}},
            "task_checksum": "b" * 64,
            "exception_info": None,
            "verifier_result": {"rewards": {"reward": 1}},
        },
    )
    monkeypatch.setattr(evidence, "_construction_context", lambda task_dir: context)
    return job, result, input_path, context


@pytest.mark.parametrize("mutation", ["health", "trial_id", "job_id", "unrecorded_regrade"])
def test_workspace_regrade_requires_complete_source_result(tmp_path, workspace_source, mutation):
    job, result, _, context = workspace_source
    native = evidence.read(result)
    if mutation == "health":
        del native["exception_info"]
    elif mutation == "trial_id":
        del native["id"]
    elif mutation == "job_id":
        del native["config"]["job_id"]
    else:
        native["config"]["source_trial"] = {"action": "regrade", "trial_id": "older-trial"}
    write(result, native)
    (tmp_path / "task/tests/test.sh").write_text("exit 1\n")
    with pytest.raises(shared_evidence._workspace.ContractError):
        shared_evidence._regrade_inputs(tmp_path, tmp_path / "private/jobs/regraded", job, "current", "c" * 64, context)


@pytest.mark.parametrize("mutation", ["tests_tree_sha256", "task_tree_without_tests_sha256", "unrecorded_regrade"])
def test_workspace_fresh_validation_rejects_inconsistent_inputs(tmp_path, workspace_source, mutation):
    job, result, input_path, _ = workspace_source
    inputs = evidence.read(input_path)
    native = evidence.read(result)
    if mutation == "unrecorded_regrade":
        native["config"]["source_trial"] = {"action": "regrade", "trial_id": "older-trial"}
    else:
        inputs[mutation] = "sha256:" + "0" * 64
        write(input_path, inputs)
    with pytest.raises(shared_evidence._workspace.ContractError):
        shared_evidence._validate_run_inputs(tmp_path, job, "oracle", native, inputs["task_tree_sha256"], "b" * 64)


def test_partial_registration_disposal_cannot_hide_a_failed_attempt_during_repair(tmp_path, prepared, monkeypatch):
    task, contract, manifest = prepared
    receipts = complete(tmp_path, manifest)
    failed = execute(tmp_path, manifest, "agent", "failed-attempt", error="synthetic tool failure")
    before = manifest.read_bytes()
    with pytest.raises(evidence.EvidenceError, match="all registered closure attempts"):
        evidence.verify_receipts(receipts, "cover-read")
    with monkeypatch.context() as patch:

        def interrupted(*args, **kwargs):
            discarded = next(tmp_path.glob(".repair-discard-*/registrations"))
            (discarded / (hashlib.sha256(b"failed-attempt").hexdigest() + ".json")).unlink()
            assert len(list(discarded.iterdir())) == len(receipts)
            with pytest.raises(evidence.EvidenceError, match="complete attempt ledger"):
                evidence.verify_receipts(receipts, "cover-read")
            raise OSError("interrupted before replacing the current manifest")

        patch.setattr(evidence, "atomic_write_json", interrupted)
        with pytest.raises(OSError, match="interrupted"):
            evidence.prepare(task, contract, manifest, manifest, "Correct a grading requirement")
    assert manifest.read_bytes() == before
    assert all(path.is_file() for path in [*receipts, failed])
    assert not list(tmp_path.glob(".repair-discard-*"))
    with pytest.raises(evidence.EvidenceError, match="complete attempt ledger"):
        evidence.verify_receipts(receipts, "cover-read")
    evidence.prepare(task, contract, manifest, manifest, "Correct a grading requirement")
    assert evidence.manifest_at(manifest)["repair_count"] == 1


def test_in_place_repair_preserves_native_files_placed_in_the_registration_directory(tmp_path, prepared):
    task, contract, manifest = prepared
    complete(tmp_path, manifest)
    before = manifest.read_bytes()
    native = Path(evidence.read(manifest)["attempts"]) / "native-result.json"
    native.write_text("Native result that must be preserved.")
    with pytest.raises(evidence.EvidenceError, match="keep native results"):
        evidence.prepare(task, contract, manifest, manifest, "Correct a grading requirement")
    assert manifest.read_bytes() == before
    assert native.read_text() == "Native result that must be preserved."


def test_legacy_repair_count_does_not_require_superseded_manifests(tmp_path, prepared):
    task, contract, manifest = prepared
    value = evidence.read(manifest)
    value.pop("repair_count")
    value["repairs"] = [{"manifest": str(tmp_path / "gone.json"), "sha256": "0" * 64, "reason": "Old repair"}]
    write(manifest, value)
    assert evidence.manifest_at(manifest) == value
    evidence.prepare(task, contract, manifest, manifest, "Continue the unfinished repair")
    assert evidence.manifest_at(manifest)["repair_count"] == 2


def test_current_repair_reason_is_checked_while_older_manifests_remain_readable(tmp_path, prepared):
    task, contract, manifest = prepared
    evidence.prepare(task, contract, manifest, manifest, "Correct the expected result")
    value = evidence.read(manifest)
    assert value.pop("repair_reason") == "Correct the expected result"
    write(manifest, value)
    assert evidence.manifest_at(manifest)["repair_count"] == 1
    value["repair_reason"] = None
    write(manifest, value)
    with pytest.raises(evidence.EvidenceError, match="invalid current repair reason"):
        evidence.manifest_at(manifest)


def test_harbor_native_identity_checks_revision_and_exceptions():
    manifest = {"contract": {"provider": "harbor", "native_run_id": "/id"}, "harbor_task_checksum": "expected"}
    native = {"id": "trial", "task_checksum": "expected", "exception_info": None}
    assert evidence.native_identity(native, manifest) == "trial"
    for changed, match in [({"task_checksum": "wrong"}, "checksum"), ({"exception_info": {}}, "exception")]:
        with pytest.raises(evidence.EvidenceError, match=match):
            evidence.native_identity({**native, **changed}, manifest)


def test_duplicate_native_run_cannot_be_renamed_into_a_repeat(tmp_path, prepared):
    receipts = complete(tmp_path, prepared[2])
    result = tmp_path / "repeat-2-native.json"
    native = evidence.read(result)
    native["rollout_id"] = "repeat-1"
    write(result, native)
    receipt = evidence.read(receipts[-1])
    receipt["native_run_id"] = "repeat-1"
    receipt["result_sha256"] = evidence.digest(result)
    write(receipts[-1], receipt)
    with pytest.raises(evidence.EvidenceError, match="native run identity is changed or reused"):
        evidence.verify_receipts(receipts, "cover-read")


def test_task_edit_during_execution_invalidates_receipt(tmp_path, prepared):
    task, _, manifest = prepared
    receipt = execute(
        tmp_path,
        manifest,
        "reference",
        "mutation",
        command=[
            sys.executable,
            "-c",
            f'from pathlib import Path; Path({str(task / "instruction.md")!r}).write_text("changed")',
        ],
    )
    assert evidence.read(receipt)["status"] == "infrastructure_error"
    assert "task revision changed" in evidence.read(receipt)["error"]


def test_contract_demands_applicable_control_decisions(tmp_path, prepared):
    task, contract_path, _ = prepared
    contract = evidence.read(contract_path)
    del contract["alternative_not_applicable"]
    write(contract_path, contract)
    with pytest.raises(evidence.EvidenceError, match="include alternative case"):
        evidence.prepare(task, contract_path, tmp_path / "new.json")


def test_diagnostic_cannot_replace_reference_control(tmp_path, prepared):
    task, contract_path, _ = prepared
    contract = evidence.read(contract_path)
    contract["cases"]["probe"] = {**contract["cases"]["reference"], "kind": "diagnostic"}
    write(contract_path, contract)
    manifest = tmp_path / "with-diagnostic.json"
    evidence.prepare(task, contract_path, manifest)
    receipt = execute(tmp_path, manifest, "probe", "probe")
    with pytest.raises(evidence.EvidenceError, match="diagnostic or unknown"):
        evidence.verify_receipts([receipt], "cover-read")


def test_harbor_preparation_without_harbor_is_an_evidence_error(tmp_path, prepared, monkeypatch):
    task, contract_path, _ = prepared
    monkeypatch.setitem(sys.modules, "harbor.models.task.task", None)
    with pytest.raises(evidence.EvidenceError, match="Harbor Python runtime"):
        evidence.prepare(task, contract_path, tmp_path / "without-harbor.json")


def test_numeric_bands_and_missing_health_are_distinct():
    assert evidence.assertions({"score": 0.8}, [{"pointer": "/score", "min": 0.7, "max": 1}])
    assert not evidence.assertions({"score": True}, [{"pointer": "/score", "min": 0, "max": 1}])
    with pytest.raises(evidence.EvidenceError, match="missing result field"):
        evidence.assertions({}, [{"pointer": "/error", "equals": None}])


def test_documented_harbor_trial_flow_is_accepted(tmp_path, monkeypatch):
    """Recorded `harbor trial start` runs, measured as documented, satisfy verify end to end."""
    import harbor.models.task.task

    monkeypatch.setattr(harbor.models.task.task, "Task", lambda path: SimpleNamespace(checksum="synthetic"))
    ethos = tmp_path / "ETHOS.md"
    ethos.write_text("# Ethos\n\n## Tools\n\n- customer.lookup\n")
    audit = tmp_path / ".eval-author/audit.md"
    audit.parent.mkdir()
    audit.write_text(
        (AUDIT_SKILL / "templates/audit.md")
        .read_text()
        .replace(
            'sha256: "sha256:<replace-with-64-hex-digest>"',
            f"sha256: sha256:{hashlib.sha256(ethos.read_bytes()).hexdigest()}",
        )
    )
    gap = {"name": "customer.lookup", "kind": "tool", "reason": "not_covered_by_any_input_report"}
    before = write(tmp_path / "before.json", {"uncovered_items": [gap]})
    task_id = pipeline._task_slug_for_target(evidence.read(before), "customer.lookup")
    task = tmp_path / ".eval-author/task-drafts" / task_id
    task.mkdir(parents=True)
    (task / "instruction.md").write_text("Look up the customer.")
    measurements = tmp_path / ".eval-author/task-measurements" / task_id
    contract = {
        "task_id": task_id,
        "provider": "harbor",
        "native_run_id": "/id",
        "alternative_not_applicable": "The lookup has one correct result.",
        "side_effect_not_applicable": "The task only reads customer data.",
        "cases": {
            kind: {
                "kind": kind,
                "requirement": "Look up the requested customer.",
                "health": [{"pointer": "/exception_info", "equals": None}],
                "checks": [{"pointer": "/verifier_result/rewards/reward", "min": reward, "max": reward}],
            }
            for kind, reward in (("reference", 1), ("incorrect", 0), ("empty", 0), ("agent", 1))
        },
    }
    baseline_trace = tmp_path / "baseline.json"
    write(
        baseline_trace,
        {
            "schema_version": "ATIF-v1.7",
            "session_id": "baseline",
            "agent": {"name": "example-agent", "version": "1.0.0"},
            "steps": [{"step_id": 1, "source": "user", "message": "Read the customer."}],
        },
    )
    for script, args in [
        (
            "measure.py",
            [
                "--trace",
                baseline_trace,
                "--evidence-class",
                "agent_under_test",
                "--task-id",
                task_id,
                "--out-dir",
                tmp_path / "baseline",
            ],
        ),
        ("report.py", ["--coverage-dir", tmp_path / "baseline", "--out", before]),
    ]:
        command = [sys.executable, AUDIT_SKILL / "scripts/audit_spec" / script, "--audit", audit, *args]
        completed = subprocess.run([str(part) for part in command], capture_output=True, text=True)
        assert completed.returncode == 0, completed.stdout + completed.stderr

    proposal = tmp_path / "proposal.md"
    proposal.write_text("Look up the customer.")
    pipeline._write_lineage(task, before, proposal, "customer.lookup")
    manifest = measurements / "revision-1.json"
    evidence.prepare(task, write(tmp_path / "contract.json", contract), manifest)
    fake_harbor = tmp_path / "fake_harbor.py"
    fake_harbor.write_text(FAKE_HARBOR)
    trials = tmp_path / ".eval-author/jobs" / task_id
    receipts = []
    for case, run_id, agent in [
        ("reference", "reference-1", "oracle"),
        ("incorrect", "incorrect-1", "wrong-answer"),
        ("empty", "empty-1", "nop"),
        ("agent", "repeat-1", "example-agent"),
        ("agent", "repeat-2", "example-agent"),
    ]:
        command = [sys.executable, str(fake_harbor), "trial", "start", "-p", str(task), "-a", agent]
        command += ["--trial-name", run_id, "--trials-dir", str(trials)]
        trace = trials / run_id / "agent/trajectory.json" if case == "agent" else None
        receipt = measurements / f"{run_id}.json"
        recorded = evidence.run(manifest, case, run_id, receipt, trials / run_id / "result.json", trace, command)
        assert recorded["status"] == "passed", recorded
        receipts.append(receipt)

    after = []
    for run_id in ("repeat-1", "repeat-2"):
        out = measurements / run_id
        report = measurements / f"{run_id}-report.json"
        for script, args in [
            (
                "measure.py",
                ["--trial-dir", trials / run_id, "--task-id", task_id, "--run-id", run_id, "--out-dir", out],
            ),
            ("report.py", ["--coverage-dir", out, "--out", report]),
        ]:
            command = [sys.executable, AUDIT_SKILL / "scripts/audit_spec" / script, "--audit", audit, *args]
            completed = subprocess.run([str(part) for part in command], capture_output=True, text=True)
            assert completed.returncode == 0, completed.stdout + completed.stderr
        after.append(report)

    command = [sys.executable, str(SCRIPTS / "task_pipeline.py"), "verify", "--before", str(before)]
    command += ["--target", "customer.lookup", *(f"--after={path}" for path in after)]
    command += [f"--evidence={path}" for path in receipts]
    completed = subprocess.run(command, capture_output=True, text=True)
    verdict = json.loads(completed.stdout)
    assert completed.returncode == 0 and verdict["accepted"] and verdict["coverage_closed"], completed.stdout
    saved = write(tmp_path / "acceptance.json", verdict)
    code, rechecked = pipeline._check_acceptance(saved)
    assert code == 0 and rechecked == verdict
    code, measured = pipeline._acceptance_integrity(saved)
    assert code == 0 and measured["acceptance_integrity"]["status"] == "consistent"
    assert measured["acceptance_integrity"]["required_verification"]["current_pass"] == 3
    proposal.write_text("Changed requirement.")
    with pytest.raises(pipeline.PipelineError, match="proposal content changed"):
        pipeline._check_acceptance(saved)


def test_copied_trace_content_is_not_a_fresh_trial(tmp_path, prepared):
    _, _, manifest = prepared
    receipts = complete(tmp_path, manifest)
    first, second = [evidence.read(path) for path in receipts[-2:]]
    Path(second["trace"]).write_bytes(Path(first["trace"]).read_bytes())
    second["trace_sha256"] = evidence.digest(Path(second["trace"]))
    write(receipts[-1], second)
    with pytest.raises(evidence.EvidenceError, match="trace is changed or reused"):
        evidence.verify_receipts(receipts, "cover-read")


def test_failed_attempt_cannot_be_omitted_from_acceptance(tmp_path, prepared):
    _, _, manifest = prepared
    failed = execute(tmp_path, manifest, "agent", "failed", reward=0)
    receipts = complete(tmp_path, manifest)
    with pytest.raises(evidence.EvidenceError, match="all registered closure attempts"):
        evidence.verify_receipts(receipts, "cover-read")
    with pytest.raises(evidence.EvidenceError, match="not passed"):
        evidence.verify_receipts([failed, *receipts], "cover-read")


def test_unbound_legacy_report_is_not_acceptance_evidence(tmp_path):
    report = write(tmp_path / "legacy.json", {"covered": ["read"]})
    with pytest.raises(pipeline.PipelineError, match="unbound legacy coverage"):
        pipeline._verify_coverage_binding(report)


@pytest.mark.parametrize("reward", [0, 1])
def test_diagnostic_attempt_does_not_poison_closure(tmp_path, prepared, reward):
    task, contract_path, manifest = prepared
    contract = evidence.read(contract_path)
    contract["cases"]["diagnostic"] = {**contract["cases"]["agent"], "kind": "diagnostic"}
    write(contract_path, contract)
    diagnostic_manifest = tmp_path / "with-diagnostic.json"
    evidence.prepare(task, contract_path, diagnostic_manifest)
    diagnostic = execute(tmp_path, diagnostic_manifest, "diagnostic", "probe", reward=reward)
    receipts = complete(tmp_path, diagnostic_manifest)
    assert len(evidence.verify_receipts(receipts, "cover-read")) == 2
    assert diagnostic.exists()
    ledger_entry = Path(evidence.read(diagnostic_manifest)["attempts"]) / (
        hashlib.sha256(b"probe").hexdigest() + ".json"
    )
    legacy_entry = evidence.read(ledger_entry)
    legacy_entry.pop("kind")
    legacy_entry.pop("case")
    write(ledger_entry, legacy_entry)
    assert len(evidence.verify_receipts(receipts, "cover-read")) == 2
    with pytest.raises(evidence.EvidenceError):
        evidence.verify_receipts([diagnostic, *receipts], "cover-read")


def test_attempt_ledger_ignores_unrelated_files_but_checks_registered_entries(tmp_path, prepared):
    _, _, manifest = prepared
    receipts = complete(tmp_path, manifest)
    ledger = Path(evidence.read(manifest)["attempts"])
    (ledger / ".DS_Store").write_bytes(b"\xff")
    (ledger / "notes.txt").write_text("Retained diagnostics")
    assert len(evidence.verify_receipts(receipts, "cover-read")) == 2
    (ledger / ("a" * 64 + ".json")).write_text("{")
    with pytest.raises(evidence.EvidenceError, match="cannot read evidence"):
        evidence.verify_receipts(receipts, "cover-read")


def test_nop_cannot_be_the_realistic_incorrect_control(tmp_path, prepared):
    _, _, manifest = prepared
    for kind, expected in (("incorrect", "infrastructure_error"), ("empty", "passed")):
        native = {"config": {"agent": {"name": "nop"}}, "rollout_id": kind, "reward": 0, "error": None}
        result = tmp_path / f"{kind}-native.json"
        command = [
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(result)!r}).write_text({json.dumps(native)!r})",
        ]
        receipt = evidence.read(execute(tmp_path, manifest, kind, kind, command=command))
        assert receipt["status"] == expected
        if kind == "incorrect":
            assert "wrong answer" in receipt["error"]


def native_construction(contract):
    plan = {
        "schema": shared_evidence.construction.plan_format.SCHEMA,
        "task_id": contract["task_id"],
        "provider": "harbor",
        "requirements": [
            {
                "id": "total",
                "statement": "Return the correct total.",
                "authority": {"basis": "specification", "reference": "Reviewed total rule"},
            }
        ],
        "inputs": [],
        "checks": [{"id": "total", "requirements": ["total"], "success": contract["cases"]["reference"]["checks"][0]}],
        "cases": [
            {"case": name, "observations": [{"check": "total", "expected": case["checks"][0]}]}
            for name, case in contract["cases"].items()
        ],
        "prerequisites": [],
    }
    return shared_evidence.construction.from_plan(plan, contract)


def test_construction_binding_rechecked_and_historical_manifest_readable(prepared, tmp_path):
    PlanError = shared_evidence.construction.PlanError
    task, contract_path, legacy_path = prepared
    contract = evidence.read(contract_path)
    plan = native_construction(contract)
    plan_path = write(tmp_path / "construction.json", plan)
    bound_path = tmp_path / "bound.json"
    evidence.prepare(task, contract_path, bound_path, construction_plan=plan_path)
    assert evidence.manifest_at(bound_path)["construction"]["assessment"]["execution"] == "unmeasured"
    receipts = complete(tmp_path, bound_path)
    evidence.verify_receipts(receipts, contract["task_id"])
    plan_path.write_text(json.dumps(plan, indent=2))
    with pytest.raises(PlanError, match="changed"):
        evidence.verify_receipts(receipts, contract["task_id"])
    with pytest.raises(PlanError, match="changed"):
        execute(tmp_path, bound_path, "reference", "changed-plan")
    # Original v1 manifests did not carry construction claims or require a plan.
    legacy = evidence.read(legacy_path)
    legacy["schema"] = evidence.SCHEMA
    del legacy["construction"]
    write(legacy_path, legacy)
    assert evidence.manifest_at(legacy_path) == legacy
    # New manifests cannot silently omit their construction status.
    legacy["schema"] = evidence.MANIFEST_SCHEMA
    write(legacy_path, legacy)
    with pytest.raises(PlanError):
        evidence.manifest_at(legacy_path)
