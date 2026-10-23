# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Behavioral regressions for construction qualification, using synthetic jobs."""

import argparse
import copy
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL / "scripts"))
import construction as contract  # noqa: E402
import qualification_runtime as runtime  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "construction_test_helpers", SKILL.parents[1] / "tests/test_trace_environment.py"
)
assert spec is not None and spec.loader is not None
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
driver = helpers._TRACE_ENVIRONMENT
REVIEW = {"reviewer_kind": "agent", "note": "Reviewed this synthetic fixture against its public requirements."}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def legacy(root):
    (root / "private/workspace.json").unlink(missing_ok=True)
    summary = json.loads((root / "summary.json").read_text())
    summary["schema"] = driver.SCHEMA
    summary["environment"].pop("qualification", None)
    save(root / "summary.json", summary)


def probe(key, kind, script, root):
    return {
        "id": key,
        "kind": kind,
        "argv": [sys.executable, "-c", script],
        "cwd": str(root),
        "timeout_seconds": 2,
        "json_pointer": "/reached",
        "equals": True,
        "assertion_path": "task/tests/test.sh",
    }


def workspace(tmp_path):
    root, _ = helpers._workspace(tmp_path)
    legacy(root)
    helpers._candidate(root)
    helpers._ready_environment(root, record_validation=False)
    candidate = json.loads((root / "candidate.json").read_text())
    candidate["requirements"] = [
        {"description": "Repair the first record.", "evidence_steps": [1]},
        {"description": "Preserve the dependent record.", "evidence_steps": [1]},
    ]
    candidate["state_basis"] = "reconstructed"
    save(root / "candidate.json", candidate)
    summary = json.loads((root / "summary.json").read_text())
    summary["schema"] = driver.QUALIFIED_SUMMARY_SCHEMA
    summary["environment"]["qualification"] = "not_run"
    save(root / "summary.json", summary)
    tests = root / "task/tests"
    shutil.copy(SKILL / "scripts/qualification_runtime.py", tests / "qualification_runtime.py")
    probes = [
        probe("setup", "setup", "import json; print(json.dumps({'reached': True}))", root),
        probe(
            "interface",
            "reach",
            "import json; print(json.dumps(json.load(open('private/runtime-observation.json'))))",
            root,
        ),
    ]
    save(tests / "qualification-probes.json", probes)
    starter = root / "task/environment/inputs/state.txt"
    starter.parent.mkdir()
    starter.write_text("broken\n")
    controls = []
    for key, arm, requirements, fails, dependency in (
        ("incomplete", "negative", ["repair"], ["fixture-repaired"], None),
        ("equivalent", "alternative", ["repair", "preserve"], [], None),
        ("skip-repair", "bypass", ["preserve"], ["dependent-preserved"], "repair-needed"),
    ):
        controls.append(
            {
                "id": key,
                "arm": arm,
                "requirements": requirements,
                "fails": fails,
                "reaches": ["interface"],
                "dependency": dependency,
                "rationale": f"Synthetic {key} control.",
                "review": REVIEW,
            }
        )
    manifest = {
        "schema": contract.SCHEMA,
        "candidate_sha256": contract.file_digest(root, "candidate.json"),
        "trace_sha256": contract.file_digest(root, "safe/trace.atif.json"),
        "requirements": [
            {"id": "repair", "candidate_index": 0, "checks": ["fixture-repaired"]},
            {"id": "preserve", "candidate_index": 1, "checks": ["dependent-preserved"]},
        ],
        "probes": probes,
        "controls": controls,
        "dependencies": [
            {"id": "repair-needed", "prerequisite": "repair", "dependent": "preserve", "bypass_control": "skip-repair"}
        ],
        "starting_state": {
            "cutoff_step_id": 1,
            "roots": ["task/environment/inputs"],
            "artifacts": [
                {
                    "path": "task/environment/inputs/state.txt",
                    "sha256": contract.file_digest(root, "task/environment/inputs/state.txt"),
                    "basis": "authored",
                    "source": None,
                    "edits": [],
                    "review": REVIEW,
                }
            ],
            "external_inputs": [],
            "no_files_reason": None,
            "review": REVIEW,
        },
        "review": REVIEW,
    }
    save(root / contract.PATH, manifest)
    helpers._record_reproducibility(root)
    return root, manifest


def proof(root, manifest, *, fail_setup=False, wrong_bypass=False, alternative_fails=False, no_progress=False):
    jobs = {arm: [] for arm in ("nop", "oracle", "negative", "alternative", "bypass")}
    for number, arm in enumerate(("nop", "nop", "oracle", "oracle", "negative", "alternative", "bypass")):
        name = f"new-{arm}-{number}"
        job = root / "private/jobs" / name
        custom = next((c for c in manifest["controls"] if c["arm"] == arm), None)
        extra = []
        if custom:
            source = root / "private" / f"{custom['id']}.py"
            source.write_text(f"# synthetic control: {custom['id']}\n")
            extra = [
                "--control-id",
                custom["id"],
                "--control-agent",
                f"{custom['id'].replace('-', '_')}:Agent",
                "--control-source",
                str(source),
                "--control-rationale",
                custom["rationale"],
            ]
        code, result = helpers._run(
            "record-run-inputs", "--task-dir", str(root), "--arm", arm, "--job-dir", str(job), *extra
        )
        assert code == 0, result
        trial = job / "task__trial"
        (trial / "verifier").mkdir(parents=True)
        reward = 1 if arm in ("oracle", "alternative") else 0
        if arm == "alternative" and alternative_fails:
            reward = 0
        statuses = ["PASS", "PASS"] if reward else ["FAIL", "FAIL"]
        if arm == "bypass" and wrong_bypass:
            statuses = ["FAIL", "PASS"]
        (trial / "verifier/results").write_text(
            f"fixture-repaired\t{statuses[0]}\ndependent-preserved\t{statuses[1]}\n"
        )
        save(
            trial / "result.json",
            {
                "task_checksum": driver._harbor_task_checksum(root),
                "verifier_environment_mode": "separate",
                "verifier_result": {"rewards": {"reward": reward}},
                "exception_info": None,
                "config": {"job_id": name, "agent": {"name": extra[3] if custom else arm}},
            },
        )
        # Synthetic provider state outside the frozen task bundle. The real
        # producer executes probes against this observation for each trial.
        save(root / "private/runtime-observation.json", {"reached": arm != "nop" or no_progress})
        runtime.run_probes(manifest["probes"], trial / "verifier/qualification.json")
        if arm == "negative" and fail_setup:
            receipt = json.loads((trial / "verifier/qualification.json").read_text())
            receipt["probes"][0]["exit_code"] = 1
            receipt["probes"][0]["failure"] = "nonzero_exit"
            receipt["probes"][0]["observation"] = None
            save(trial / "verifier/qualification.json", receipt)
        jobs[arm].append(job)
    return jobs


def validate(root, jobs):
    return driver._validation_from_jobs(
        root,
        jobs["nop"],
        jobs["oracle"],
        jobs["negative"],
        "synthetic",
        alternative_job_dirs=jobs["alternative"],
        bypass_job_dirs=jobs["bypass"],
    )


def test_new_workspace_requires_construction_even_after_manifest_deletion(tmp_path):
    root, manifest = workspace(tmp_path)
    assert contract.validate(root)["provenance"]["basis_counts"]["authored"] == 1
    (root / contract.PATH).unlink()
    with pytest.raises(driver.ContractError, match="construction"):
        driver._construction_context(root)
    code, result = helpers._run("validate-task", "--task-dir", str(root))
    assert code == 1 and any(i["code"] == "construction_contract" for i in result["issues"])


@pytest.mark.parametrize("fault", ["setup", "bypass", "alternative", "no-progress", None])
def test_qualification_depends_on_meaningful_control_outcomes(tmp_path, fault):
    root, manifest = workspace(tmp_path)
    jobs = proof(
        root,
        manifest,
        fail_setup=fault == "setup",
        wrong_bypass=fault == "bypass",
        alternative_fails=fault == "alternative",
        no_progress=fault == "no-progress",
    )
    report = validate(root, jobs)
    assert report["passed"] is (fault is None)
    assert report["qualification"]["status"] == ("passed" if fault is None else "failed")
    assert report["qualification"]["difficulty"] == "unmeasured"
    assert report["qualification"]["tested_dependencies"] == 1
    save(root / "validation.json", report)
    assert driver._validate_validation(root) == report


def test_stale_manifest_invalidates_pre_run_binding(tmp_path):
    root, manifest = workspace(tmp_path)
    jobs = proof(root, manifest)
    manifest["review"]["note"] = "Changed reviewed requirements after execution."
    save(root / contract.PATH, manifest)
    with pytest.raises(driver.ContractError, match="pre-run construction"):
        validate(root, jobs)


def test_missing_control_is_not_qualified(tmp_path):
    root, manifest = workspace(tmp_path)
    jobs = proof(root, manifest)
    jobs["bypass"] = []
    with pytest.raises(driver.ContractError, match="every declared control"):
        validate(root, jobs)


@pytest.mark.parametrize(
    "fault",
    ["mapping", "cycle", "unknown-check", "extra-input", "changed-input", "future", "recorded-authored", "symlink"],
)
def test_invalid_construction_is_rejected(tmp_path, fault):
    root, manifest = workspace(tmp_path)
    artifact = manifest["starting_state"]["artifacts"][0]
    if fault == "mapping":
        manifest["requirements"].pop()
    elif fault == "cycle":
        extra = copy.deepcopy(manifest["controls"][-1])
        extra.update(
            id="skip-preserve", dependency="preserve-needed", requirements=["repair"], fails=["fixture-repaired"]
        )
        manifest["controls"].append(extra)
        manifest["dependencies"].append(
            {
                "id": "preserve-needed",
                "prerequisite": "preserve",
                "dependent": "repair",
                "bypass_control": "skip-preserve",
            }
        )
    elif fault == "unknown-check":
        manifest["controls"][0]["fails"] = ["made-up"]
    elif fault == "extra-input":
        (root / "task/environment/inputs/unlisted.txt").write_text("unlisted")
    elif fault == "changed-input":
        (root / artifact["path"]).write_text("changed")
    elif fault == "future":
        artifact.update(basis="observed", source={"step_id": 2, "pointer": "/message"})
    elif fault == "recorded-authored":
        candidate = json.loads((root / "candidate.json").read_text())
        candidate["state_basis"] = "recorded"
        save(root / "candidate.json", candidate)
        manifest["candidate_sha256"] = contract.file_digest(root, "candidate.json")
    elif fault == "symlink":
        path = root / artifact["path"]
        path.unlink()
        path.symlink_to(root / "candidate.json")
    save(root / contract.PATH, manifest)
    with pytest.raises(contract.ConstructionError):
        contract.validate(root)


def test_observation_and_exact_reversal_use_trace_order_not_step_numbers(tmp_path):
    root, manifest = workspace(tmp_path)
    trace = {
        "steps": [
            {"step_id": 90, "message": "broken\n"},
            {"step_id": 4, "edit": {"before": "broken", "after": "fixed"}},
            {"step_id": 2, "message": "fixed\n"},
        ]
    }
    save(root / "safe/trace.atif.json", trace)
    manifest["trace_sha256"] = contract.file_digest(root, "safe/trace.atif.json")
    state = manifest["starting_state"]
    state["cutoff_step_id"] = 90
    artifact = state["artifacts"][0]
    artifact.update(basis="observed", source={"step_id": 90, "pointer": "/message"})
    save(root / contract.PATH, manifest)
    assert contract.validate(root)["provenance"]["basis_counts"]["observed"] == 1
    artifact.update(
        basis="reversed_edit",
        source={"step_id": 2, "pointer": "/message"},
        edits=[
            {"before": {"step_id": 4, "pointer": "/edit/before"}, "after": {"step_id": 4, "pointer": "/edit/after"}}
        ],
    )
    save(root / contract.PATH, manifest)
    assert contract.validate(root)["provenance"]["basis_counts"]["reversed_edit"] == 1
    artifact["edits"][0]["after"]["pointer"] = "/edit/before"
    save(root / contract.PATH, manifest)
    with pytest.raises(contract.ConstructionError, match="exactly once"):
        contract.validate(root)


@pytest.mark.parametrize(
    "script,failure",
    [
        ("raise ImportError('missing')", "nonzero_exit"),
        ("print('not-json')", "invalid_observation"),
        ("import time; time.sleep(2)", "timeout"),
        ("print('x' * 300000)", "output_limit"),
    ],
)
def test_runtime_observes_real_execution_failures(tmp_path, script, failure):
    definition = probe("reach", "reach", script, tmp_path)
    definition["timeout_seconds"] = 0.1
    row = runtime.run_probe(definition)
    assert row["failure"] == failure
    assert row["observation"] is None
    if failure == "nonzero_exit":
        assert row["exit_code"] != 0
        assert b"ImportError: missing" in row["output_bytes"]


def test_runtime_records_observed_value_and_refuses_stale_output(tmp_path):
    definition = probe("reach", "reach", "import json; print(json.dumps({'reached': False}))", tmp_path)
    output = tmp_path / "qualification.json"
    runtime.run_probes([definition], output)
    row = json.loads(output.read_text())["probes"][0]
    assert row["observation"] is False
    assert row["exit_code"] == 0
    with pytest.raises(FileExistsError):
        runtime.run_probes([definition], output)


def test_complete_lifecycle_and_evidence_tampering(tmp_path):
    root, manifest = workspace(tmp_path)
    jobs = proof(root, manifest)
    save(root / "validation.json", validate(root, jobs))
    helpers._review_privacy(root)
    code, result = helpers._run("finalize", "--task-dir", str(root), "--status", "candidate", "--human-reviewed")
    assert code == 0 and result["qualification"] == "passed", result
    assert result["environment_status"] == "ready"
    code, checked = helpers._run("check", "--task-dir", str(root))
    assert code == 0 and checked["qualification"] == "passed", checked
    output = tmp_path / "public"
    output.mkdir()
    driver._write_publication(root, output)
    product = json.loads((output / "result.json").read_text())
    assert product["construction_qualification"] == "passed"
    assert product["technical_validation"]["qualification"]["status"] == "passed"
    assert not (output / contract.PATH).exists()
    assert "candidate_sha256" not in json.dumps(product)
    batch_file = tmp_path / "batch.json"
    save(
        batch_file,
        {
            "schema": driver.BATCH_SCHEMA,
            "members": [{"task_id": root.name, "atif": "source.json", "source_kind": "atif"}],
        },
    )
    args = argparse.Namespace(root=root.parent, manifest=batch_file)
    assert driver._batch_status(args)["members"][0]["qualification"] == "passed"
    evidence_path = jobs["negative"][0] / "task__trial/verifier/qualification.json"
    original = evidence_path.read_bytes()
    evidence_path.unlink()
    assert helpers._run("check", "--task-dir", str(root))[0] == 1
    assert driver._batch_status(args)["members"][0]["status"] == "invalid"
    with pytest.raises(driver.ContractError):
        driver._write_publication(root, tmp_path / "invalid-public")
    evidence_path.write_bytes(original)
    assert helpers._run("check", "--task-dir", str(root))[0] == 0
    (root / contract.PATH).unlink()
    assert helpers._run("check", "--task-dir", str(root))[0] == 1
    with pytest.raises(driver.ContractError):
        driver._write_publication(root, tmp_path / "missing-manifest-public")


def test_unrun_candidate_does_not_claim_qualification(tmp_path):
    root, _ = workspace(tmp_path)
    helpers._review_privacy(root)
    code, result = helpers._run("finalize", "--task-dir", str(root), "--status", "candidate", "--human-reviewed")
    assert code == 0 and result["qualification"] == "not_run", result
    assert result["environment_status"] == "unproven"
    summary = json.loads((root / "summary.json").read_text())
    summary["environment"]["qualification"] = "passed"
    save(root / "summary.json", summary)
    assert helpers._run("check", "--task-dir", str(root))[0] == 1


def test_no_candidate_has_no_construction_requirement(tmp_path):
    root, _ = helpers._workspace(tmp_path)
    summary = json.loads((root / "summary.json").read_text())
    summary["schema"] = driver.QUALIFIED_SUMMARY_SCHEMA
    summary["environment"]["qualification"] = "not_run"
    save(root / "summary.json", summary)
    helpers._candidate(root, status="no_candidate")
    code, result = helpers._run("finalize", "--task-dir", str(root), "--status", "no_candidate")
    assert code == 0 and result["qualification"] == "not_applicable", result
    assert helpers._run("check", "--task-dir", str(root))[0] == 0


def test_historical_workspace_rejects_partial_in_place_upgrade(tmp_path):
    root, _ = workspace(tmp_path)
    legacy(root)
    with pytest.raises(driver.ContractError, match="initialize a new workspace"):
        driver._construction_context(root)


def test_runtime_exercises_real_component_and_detects_broken_setup(tmp_path):
    component = tmp_path / "component.py"
    component.write_text("def calculate(x):\n    return x + 1\n")
    definition = probe(
        "component", "reach", "import component, json; print(json.dumps({'reached': component.calculate(2)}))", tmp_path
    )
    row = runtime.run_probe(definition)
    assert row["exit_code"] == 0 and row["observation"] == 3
    # A different implementation produces the same observable result.
    component.write_text("def calculate(x):\n    return sum([x, 1])\n")
    assert runtime.run_probe(definition)["observation"] == 3
    # A broken prerequisite is execution failure, not successful negative proof.
    component.write_text("import intentionally_unavailable_dependency\n")
    row = runtime.run_probe(definition)
    assert row["exit_code"] != 0 and row["observation"] is None


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_probe_retains_failed_receipt(tmp_path, number):
    definition = probe("reach", "reach", f"print('{{\"reached\": {number}}}')", tmp_path)
    output = tmp_path / "qualification.json"
    runtime.run_probes([definition], output)
    receipt = contract.load(tmp_path, "qualification.json")
    assert isinstance(receipt, dict)
    assert receipt["probes"][0]["failure"] == "invalid_observation"
    assert receipt["probes"][0]["observation"] is None


def test_maximum_probe_count_receipt_remains_readable(tmp_path):
    definitions = [
        probe(f"probe-{i}", "reach", "import json; print(json.dumps({'reached': 'x'*250000}))", tmp_path)
        for i in range(32)
    ]
    runtime.run_probes(definitions, tmp_path / "qualification.json")
    receipt = contract.load(tmp_path, "qualification.json")
    assert isinstance(receipt, dict)
    assert len(receipt["probes"]) == 32
    assert all(row["failure"] == "observation_limit" and row["observation"] is None for row in receipt["probes"])


def test_probe_numeric_type_mismatch_rejected_before_execution(tmp_path):
    root, manifest = workspace(tmp_path)
    probes = json.loads((root / "task/tests/qualification-probes.json").read_text())
    probes[0]["timeout_seconds"] = float(probes[0]["timeout_seconds"])
    save(root / "task/tests/qualification-probes.json", probes)
    with pytest.raises(contract.ConstructionError, match="probe definitions differ"):
        contract.validate(root)


def test_new_init_marker_rejects_summary_downgrade(tmp_path):
    code, result = helpers._run(
        "init", "--root", str(tmp_path / ".eval-author/.working/trace-environments"), "--task-id", "new"
    )
    assert code == 0
    root = Path(result["task_dir"])
    summary = json.loads((root / "summary.json").read_text())
    summary["schema"] = driver.SCHEMA
    summary["environment"].pop("qualification")
    save(root / "summary.json", summary)
    with pytest.raises(driver.ContractError, match="forbids summary schema downgrade"):
        driver._load_summary(root)


def test_alternative_cannot_duplicate_oracle(tmp_path):
    root, _ = workspace(tmp_path)
    source = root / "private/alternative.py"
    source.write_bytes((root / "task/solution/solve.sh").read_bytes())
    with pytest.raises(driver.ContractError, match="must not copy an oracle"):
        driver._proof_control(root, "custom:Agent", source, "reviewed alternative", arm="alternative")


def test_repair_preserves_contract_and_detects_archive_mutation(tmp_path):
    root, _ = workspace(tmp_path)
    before = (root / contract.PATH).read_bytes()
    driver._record_repair(
        argparse.Namespace(
            task_dir=root, reason_code="other", note="Correct the failing synthetic verifier implementation."
        )
    )
    archive = root / "private/repairs/repair-1/construction.json"
    assert archive.read_bytes() == before
    assert (root / "private/repairs/repair-1/candidate.json").read_bytes() == (root / "candidate.json").read_bytes()
    manifest = json.loads(before)
    manifest["review"]["note"] = "Changed after recording a repair."
    save(root / contract.PATH, manifest)
    report = driver._check(argparse.Namespace(task_dir=root))
    assert {"repair": 1, "file": "construction.json"} in report["contract_changes"]
    archive.write_text("{}")
    with pytest.raises(driver.ContractError, match="archive of construction.json is missing or changed"):
        driver._repairs(root)
