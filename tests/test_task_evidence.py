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
        evidence.prepare(task, contract, manifest, manifest, reason)
        current = evidence.manifest_at(manifest)
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
    evidence.prepare(task, contract, manifest, previous, "Fix the expected result")
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


def test_construction_binding_rechecked_and_historical_manifest_readable(prepared, tmp_path):
    construction = importlib.import_module("construction_plan")
    PLAN_SCHEMA = construction.SCHEMA
    PlanError = construction.PlanError

    task, contract_path, legacy_path = prepared
    contract = evidence.read(contract_path)
    plan = {
        "schema": PLAN_SCHEMA,
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
