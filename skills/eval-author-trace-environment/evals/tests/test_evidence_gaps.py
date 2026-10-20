# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Measured handoff, execution evidence and public-context regressions."""

import json
import os
import shutil

import pytest
import test_construction as construction_tests
import test_solution_review as review_tests

contract = construction_tests.contract
runtime = construction_tests.runtime
driver = construction_tests.driver
review = review_tests.review


def test_capture_helper_nonregular_file_is_rejected(tmp_path):
    root, _ = construction_tests.workspace(tmp_path)
    helper = root / "task/tests/review_runtime.py"
    os.mkfifo(helper)
    with pytest.raises(driver.ContractError, match="helper is not a bounded regular file"):
        driver._validate_task(root)


def test_misplaced_handoff_and_direct_capture(tmp_path):
    root, _ = construction_tests.workspace(tmp_path)
    config = root / "task/task.toml"
    original = config.read_text()
    config.write_text(original + '\nartifacts = ["/workspace/answer"]\n')
    with pytest.raises(driver.ContractError, match="top-level"):
        driver._validate_task(root)
    config.write_text(original)
    grader = root / "task/tests/grade.py"
    grader.write_text("from review_runtime import grade_submission as capture\ncapture({}, lambda x: {})\n")
    helper = root / "task/tests/review_runtime.py"
    helper.write_text("def grade_submission(*args): pass\n")
    driver._validate_task(root)  # An unrelated same-named implementation is outside this convention.
    shutil.copyfile(construction_tests.SKILL / "scripts/review_runtime.py", helper)
    with pytest.raises(driver.ContractError, match="handoff"):
        driver._validate_task(root)
    config.write_text('artifacts = ["/workspace/answer"]\n' + original)
    driver._validate_task(root)
    # An unused helper or unrelated string does not impose artifact handoff.
    config.write_text(original)
    grader.write_text("text = 'grade_submission'\n")
    driver._validate_task(root)


def execution_fixture(tmp_path, script):
    root = tmp_path
    directory = root / "private/jobs/trial/verifier"
    directory.mkdir(parents=True)
    definition = construction_tests.probe("reach", "reach", script, root)
    runtime.run_probes([definition], directory / "qualification.json")
    context = {"manifest": {"probes": [definition]}, "controls": {"control": {"reaches": ["reach"]}}}
    return root, directory, context


def test_retained_exception_and_rejected_receipt_downgrade(tmp_path):
    root, directory, context = execution_fixture(tmp_path, "raise RuntimeError('native checker failed')")
    result = contract.execution(root, directory.parent / "result.json", context, "control")
    assert not result["behavior_reached"]
    assert b"native checker failed" in (directory / "qualification-output/reach.bin").read_bytes()
    receipt = json.loads((directory / "qualification.json").read_text())
    receipt["schema"] = "nemo.eval_author.construction_execution.v1"
    row = receipt["probes"][0]
    row.pop("output_file")
    row.pop("output_truncated")
    row["failure"] = None  # Historical v1 represented nonzero exit this way.
    construction_tests.save(directory / "qualification.json", receipt)
    with pytest.raises(contract.ConstructionError, match="unknown execution receipt schema"):
        contract.execution(root, directory.parent / "result.json", context, "control")


@pytest.mark.parametrize("mutation", ["observation", "bytes", "locator", "symlink", "status"])
def test_probe_evidence_tampering_rejected(tmp_path, mutation):
    root, directory, context = execution_fixture(tmp_path, "print('{\"reached\": true}')")
    receipt = json.loads((directory / "qualification.json").read_text())
    row = receipt["probes"][0]
    path = directory / row["output_file"]
    if mutation == "observation":
        row["observation"] = False
    elif mutation == "bytes":
        path.write_text("different")
    elif mutation == "locator":
        row["output_file"] = "../reach.bin"
    elif mutation == "symlink":
        path.unlink()
        path.symlink_to(directory / "qualification.json")
    else:
        row["exit_code"] = 1
    construction_tests.save(directory / "qualification.json", receipt)
    messages = {
        "observation": "probe observation differs from output",
        "bytes": "probe output changed or oversized",
        "locator": "invalid retained probe output",
        "symlink": "evidence symlinks are not allowed",
        "status": "inconsistent probe execution status",
    }
    with pytest.raises(contract.ConstructionError, match=messages[mutation]):
        contract.execution(root, directory.parent / "result.json", context, "control")


def test_full_probe_count_with_saturated_outputs(tmp_path):
    probes = [construction_tests.probe(f"probe-{i}", "reach", "print('x' * 300000)", tmp_path) for i in range(32)]
    output = tmp_path / "qualification.json"
    runtime.run_probes(probes, output)
    assert output.stat().st_size < contract.MAX_BYTES
    rows = json.loads(output.read_text())["probes"]
    assert all(r["failure"] == "output_limit" and r["output_truncated"] for r in rows)
    assert all((tmp_path / r["output_file"]).stat().st_size == runtime.MAX_OUTPUT_BYTES for r in rows)


def test_aggregate_observation_overflow_retains_failed_receipt(tmp_path):
    probes = [
        construction_tests.probe(
            f"probe-{i}", "reach", "import json; print(json.dumps({'reached':'x'*70000}))", tmp_path
        )
        for i in range(32)
    ]
    output = tmp_path / "qualification.json"
    runtime.run_probes(probes, output)
    receipt = contract.load(tmp_path, "qualification.json")
    assert isinstance(receipt, dict)
    assert all(row["failure"] == "observation_limit" and not row["output_truncated"] for row in receipt["probes"])


@pytest.mark.parametrize("kind", ["duplicate", "unsafe", "existing_directory", "symlink"])
def test_output_inventory_refused_before_completion(tmp_path, kind):
    probe = construction_tests.probe("reach", "reach", "print('{\"reached\":true}')", tmp_path)
    probes = [probe]
    if kind == "duplicate":
        probes.append(probe)
    elif kind == "unsafe":
        probe["id"] = "../outside"
    elif kind == "existing_directory":
        (tmp_path / "qualification-output").mkdir()
    else:
        (tmp_path / "qualification-output").symlink_to(tmp_path, target_is_directory=True)
    messages = {
        "duplicate": "probe IDs must be unique safe names",
        "unsafe": "probe IDs must be unique safe names",
        "existing_directory": "File exists",
        "symlink": "File exists",
    }
    with pytest.raises((ValueError, FileExistsError), match=messages[kind]):
        runtime.run_probes(probes, tmp_path / "qualification.json")


def context_setup(tmp_path):
    root, binding, _, assessments = review_tests.setup(tmp_path)
    source = root / "task/environment/input.csv"
    source.parent.mkdir()
    source.write_text("public input\n")
    plan = json.loads((root / "private/plan.json").read_text())
    plan["public_context"] = [
        {
            "path": "task/environment/input.csv",
            "sha256": review.sha(source.read_bytes()),
            "solver_path": "/app/input.csv",
            "purpose": "input",
            "review": {
                "reviewer_kind": "agent",
                "note": "Reviewed as solver-visible input; not private grading truth.",
            },
        }
    ]
    review_tests.save(root / "private/plan.json", plan)
    receipt = review.record(root, "private/plan.json", binding)
    assessments["cohort_sha256"] = receipt["cohort_sha256"]
    return root, binding, receipt, assessments, plan


def test_public_context_is_bound_and_citable(tmp_path):
    root, binding, receipt, assessments, _ = context_setup(tmp_path)
    review_tests.trial(root, assessments)
    assessments["judgments"][0]["requirements"][0]["citations"].append("task/environment/input.csv")
    result = review_tests.run(root, binding, receipt, assessments)
    assert result["counts"] == {"legitimate_pass": 1}
    (root / "task/environment/input.csv").write_text("changed")
    with pytest.raises(ValueError, match="context changed"):
        review_tests.run(root, binding, receipt, assessments)


def test_context_cannot_replace_trial_evidence(tmp_path):
    root, binding, receipt, assessments, _ = context_setup(tmp_path)
    review_tests.trial(root, assessments)
    assessments["judgments"][0]["requirements"][0]["citations"] = [
        "task/instruction.md",
        "task/environment/input.csv",
    ]
    with pytest.raises(ValueError, match="citation must bind"):
        review_tests.run(root, binding, receipt, assessments)


@pytest.mark.parametrize(
    "path",
    [
        "task/tests/check.py",
        "task/solution/solve.sh",
        "private/answer.txt",
        "task/environment/Dockerfile",
        "task/environment/./input.csv",
        "task/environment/../input.csv",
    ],
)
def test_context_path_allowlist(tmp_path, path):
    root, _, _, _, plan = context_setup(tmp_path)
    plan["public_context"][0]["path"] = path
    with pytest.raises(ValueError):
        review.validate_plan(root, plan)


def test_public_context_rejects_symlinks(tmp_path):
    root, _, _, _, plan = context_setup(tmp_path)
    source = root / "task/environment/input.csv"
    source.unlink()
    source.symlink_to(root / "task/instruction.md")
    with pytest.raises(ValueError, match="symlink"):
        review.validate_plan(root, plan)


@pytest.mark.parametrize("failure", ["invalid_observation", "observation_limit"])
def test_success_cannot_be_relabelled_as_failed_observation(tmp_path, failure):
    root, directory, context = execution_fixture(tmp_path, "print('{\"reached\": true}')")
    receipt = json.loads((directory / "qualification.json").read_text())
    receipt["probes"][0].update(failure=failure, observation=None)
    construction_tests.save(directory / "qualification.json", receipt)
    with pytest.raises(contract.ConstructionError, match="probe observation status differs from output"):
        contract.execution(root, directory.parent / "result.json", context, "control")


def test_capture_gate_survives_workspace_alias(tmp_path):
    root, _ = construction_tests.workspace(tmp_path)
    shutil.copyfile(construction_tests.SKILL / "scripts/review_runtime.py", root / "task/tests/review_runtime.py")
    (root / "task/tests/grade.py").write_text(
        "from review_runtime import grade_submission\ngrade_submission({}, lambda x: {})\n"
    )
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(driver.ContractError, match="requires a nonempty"):
        driver._validate_task(alias)


def test_step_handoff_is_accepted_and_nested_handoff_rejected(tmp_path):
    root, _ = construction_tests.workspace(tmp_path)
    shutil.copyfile(construction_tests.SKILL / "scripts/review_runtime.py", root / "task/tests/review_runtime.py")
    (root / "task/tests/grade.py").write_text(
        "from review_runtime import grade_submission\ngrade_submission({}, lambda x: {})\n"
    )
    config = root / "task/task.toml"
    config.write_text(config.read_text() + '\n[[steps]]\nname = "solve"\nartifacts = ["/workspace/answer"]\n')
    driver._validate_task(root)
    config.write_text(config.read_text() + '\n[steps.agent]\nartifacts = ["/workspace/answer"]\n')
    with pytest.raises(driver.ContractError, match="nested artifacts are ignored"):
        driver._validate_task(root)


def test_context_solver_paths_must_be_unique(tmp_path):
    root, _, _, _, plan = context_setup(tmp_path)
    other = root / "task/environment/other.csv"
    other.write_text("other input")
    plan["public_context"].append(
        {**plan["public_context"][0], "path": "task/environment/other.csv", "sha256": review.sha(other.read_bytes())}
    )
    with pytest.raises(ValueError, match="declare solver-visible location"):
        review.validate_plan(root, plan)


@pytest.mark.parametrize("kind", ["internal_symlink", "oversized", "invalid_python"])
def test_incomplete_capture_scan_fails_closed(tmp_path, kind):
    root, _ = construction_tests.workspace(tmp_path)
    helper = root / "task/tests/review_runtime.py"
    shutil.copyfile(construction_tests.SKILL / "scripts/review_runtime.py", helper)
    grader = root / "task/tests/grade.py"
    if kind == "internal_symlink":
        grader.symlink_to(helper)
    elif kind == "oversized":
        grader.write_bytes(b"#" * (256 * 1024 + 1))
    else:
        grader.write_text("def invalid(")
    with pytest.raises(driver.ContractError, match="submission capture inspection failed"):
        driver._validate_task(root)


def test_public_context_is_read_once_per_review(tmp_path, monkeypatch):
    root, binding, receipt, assessments, _ = context_setup(tmp_path)
    review_tests.trial(root, assessments)
    assessments["judgments"][0]["requirements"][0]["citations"].append("task/environment/input.csv")
    reads = []
    original = review.read

    def tracked(root, relative, evidence):
        if relative == "task/environment/input.csv":
            reads.append(relative)
        return original(root, relative, evidence)

    monkeypatch.setattr(review, "read", tracked)
    assert review_tests.run(root, binding, receipt, assessments)["counts"] == {"legitimate_pass": 1}
    assert len(reads) == 1
