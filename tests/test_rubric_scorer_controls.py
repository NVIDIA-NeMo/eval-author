# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/eval-author-rubric/scripts/measure.py"
sys.path.insert(0, str(ROOT / "tools"))

import run_scorer_controls as runner  # noqa: E402
from test_scorer_native import synthetic_job  # noqa: E402


def test_existing_native_panel_reaches_one_without_launch_or_summary_authority(tmp_path, monkeypatch):
    native, _ = synthetic_job(tmp_path)
    monkeypatch.setattr(native.common, "execute", lambda *a, **k: pytest.fail("native launch forbidden"))
    selected = selection_for(tmp_path, "reviewed", "native")
    result, report = cli(tmp_path, selected)
    assert result.returncode in (0, 1), report
    assert report["items"]["AC-02"]["score"] == 1
    assert report["items"]["AC-02"]["evidence_strength"] == "E2"
    assert report["items"]["AC-02"]["check_results"][1]["outcome"] == "PASS"
    assert report["measurements"]["scorer_controls"]["native"]["status"] == "passed"
    # Exercise the callable too; the process-local launch guard applies here.
    assert measure_module().measure(tmp_path, selected) == report
    assert all(report["items"][ident]["score"] is None for ident in ("AC-01", "AC-03", "AC-04"))
    (tmp_path / "scorer-native-summary.json").write_text('{"status":"passed","score":3}')
    assert cli(tmp_path, selected)[1] == report
    assert cli(tmp_path, selection_for(tmp_path, "baseline", "native"))[1]["items"]["AC-02"]["score"] == 0


def test_native_projection_cannot_substitute_expected_outcomes_after_summary(tmp_path, monkeypatch):
    synthetic_job(tmp_path)
    import scorer_native

    monkeypatch.setattr(scorer_native.common, "execute", lambda *a, **k: pytest.fail("native launch forbidden"))
    module = measure_module()
    selected = selection_for(tmp_path, "baseline", "native")
    honest = module.measure(tmp_path, selected)
    assert honest["items"]["AC-02"]["score"] == 0
    path = tmp_path / "inputs/cases.json"
    original = path.read_bytes()
    forged = json.loads(original)
    for case in forged["cases"]:
        case["expected"]["reviewed"] = case["expected"]["baseline"]
    replacement = json.dumps(forged).encode()
    read = runner.source_bytes

    def swap(selected_path, **kwargs):
        frame = inspect.currentframe()
        assert frame is not None and frame.f_back is not None
        caller = frame.f_back
        if selected_path == path and caller.f_code.co_filename.endswith("/rubric_scorer_controls.py"):
            path.write_bytes(replacement)
            try:
                return read(selected_path, **kwargs)
            finally:
                path.write_bytes(original)
        return read(selected_path, **kwargs)

    monkeypatch.setattr(runner, "source_bytes", swap)
    attacked = module.measure(tmp_path, selected)
    assert attacked["items"]["AC-02"]["score"] == 0
    assert attacked == honest
    assert path.read_bytes() == original


@pytest.mark.parametrize("mode", ["fixture", "native"])
def test_known_applicable_gaps_survive_native_health(tmp_path, mode):
    output = tmp_path / "evidence"
    if mode == "native":
        synthetic_job(output)
    else:
        retained = runner.prepare(output)
        (output / "panel.json").write_text(runner.encoded(runner.run_panel(output, retained)))
    result, report = cli(output, selection_for(output, "reviewed", mode))
    assert result.returncode in (0, 1), report
    item = report["items"]["AC-02"]
    metadata = report["measurements"]["scorer_controls"]["applicability"]
    assert metadata["scope"] == (
        "Both selected refund grading arms over the fixed scripted-event vocabulary, "
        "including the native submission/worker/result path."
    )
    assert set(metadata["unresolved_classes"]) == {
        "all-failure-stub",
        "native-submission-early-exit",
        "native-stale-success-after-later-failure",
        "native-scorer-test-tampering",
    }
    assert not {"all-failure-program-stub", "scorer-test-program-tampering"} & set(metadata["excluded_classes"])
    assert set(metadata["unresolved_classes"].values()) <= set(item["remaining_gaps"])
    assert all((gap in item["remaining_gaps"]) == (mode == "fixture") for gap in metadata["fixture_gaps"])
    assert item["score"] == 1
    assert item["evidence_strength"] == "E2"


@pytest.mark.parametrize("arm", ["baseline", "reviewed"])
def test_ac03_null_means_unselected_not_absent_evidence(tmp_path, arm):
    output = tmp_path / "fixture"
    retained = runner.prepare(output)
    (output / "panel.json").write_text(runner.encoded(runner.run_panel(output, retained)))
    _, report = cli(output, selection_for(output, arm))
    item = report["items"]["AC-03"]
    assert item["score"] is None
    assert item["status"] == "NOT_ASSESSED"
    assert item["remaining_gaps"] == [
        "AC-03 was not selected in this bounded assessment. The baseline solver-authored "
        "success defect can also support AC-03=0 under the canonical shared-finding rule; "
        "this null does not mean evidence is absent."
    ]


@pytest.mark.parametrize("mode", ["fixture", "native"])
def test_selected_root_symlink_cannot_borrow_another_evidence_root(tmp_path, mode):
    target = tmp_path / "target"
    if mode == "fixture":
        selected = runner.prepare(target)
        (target / "panel.json").write_text(runner.encoded(runner.run_panel(target, selected)))
    else:
        synthetic_job(target)
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    result, report = cli(alias, selection_for(target, "reviewed", mode))
    assert result.returncode == 2
    assert report["status"] == "invalid"
    assert "items" not in report


def test_late_revalidation_cannot_leave_a_scored_report(tmp_path, monkeypatch):
    selected = runner.prepare(tmp_path / "fixture")
    output = tmp_path / "fixture"
    (output / "panel.json").write_text(runner.encoded(runner.run_panel(output, selected)))
    module = measure_module()
    original = module.build_report

    def mutate(*args, **kwargs):
        report = original(*args, **kwargs)
        (output / "inputs/cases.json").write_text("{}")
        return report

    monkeypatch.setattr(module, "build_report", mutate)
    with pytest.raises(ValueError):
        module.measure(output, selection_for(output, "reviewed"))


def test_opt_in_demo_repeats_and_verifies_rubric_before_after(tmp_path):
    command = [sys.executable, str(ROOT / "tools/run_scorer_controls.py"), "--rubric"]
    reports = []
    for name in ("first", "second"):
        output = tmp_path / name
        result = subprocess.run(command + ["--output", str(output)], capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["rubric_comparison"] == {
            "criterion": "AC-02",
            "expected": {"baseline": 0, "reviewed": 1},
            "observed": {"baseline": 0, "reviewed": 1},
            "matched": True,
        }
        replay = subprocess.run(
            command + ["--verify-existing", str(output)], capture_output=True, text=True, timeout=90
        )
        assert replay.returncode == 0, replay.stdout
        assert json.loads(replay.stdout) == report
        reports.append(report)
    assert reports[0] == reports[1]
    path = tmp_path / "first/rubric-reviewed.json"
    forged = json.loads(path.read_text())
    forged["items"]["AC-02"]["score"] = 3
    path.write_text(json.dumps(forged))
    replay = subprocess.run(
        command + ["--verify-existing", str(tmp_path / "first")], capture_output=True, text=True, timeout=90
    )
    assert replay.returncode == 2
    assert json.loads(replay.stdout)["status"] == "invalid"


@pytest.mark.parametrize("mode", ["fixture", "native"])
def test_applicability_inventory_is_bound_to_retained_sources(tmp_path, mode):
    if mode == "fixture":
        selected = runner.prepare(tmp_path / "fixture")
        output = tmp_path / "fixture"
        (output / "panel.json").write_text(runner.encoded(runner.run_panel(output, selected)))
    else:
        output = tmp_path / "native"
        synthetic_job(output)
    path = output / "inputs/applicability.json"
    assert path.is_file(), "applicability must be a selected retained input"
    selected = selection_for(output, "reviewed", mode)
    path.write_text('{"native_paths":[]}')
    result, report = cli(output, selected)
    assert result.returncode == 2
    assert report["status"] == "invalid"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "duplicate-json",
        "symlink",
        "alias",
        "forged-credit",
        "operational",
        "stale-source",
        "selected-arm",
        "selected-root",
        "summary-only",
    ],
)
def test_native_tamper_withholds_all_scores_through_measure_cli(tmp_path, mutation):
    native, trial = synthetic_job(tmp_path)
    selected = selection_for(tmp_path, "reviewed", "native")
    _, original = cli(tmp_path, selected)
    retained_report = tmp_path / "rubric-reviewed.json"
    retained_report.write_text(json.dumps(original))
    path = trial / "verifier/checks.json"
    data = json.loads(path.read_text())
    if mutation == "missing":
        data["rows"].pop()
    elif mutation == "extra":
        data["rows"].append(data["rows"][0])
    elif mutation == "duplicate-json":
        path.write_text(path.read_text().replace("{", '{"health":"ok",', 1))
    elif mutation == "symlink":
        moved = trial / "checks-copy.json"
        path.rename(moved)
        path.symlink_to(moved)
    elif mutation == "alias":
        (trial.parent / "alias").symlink_to(trial, target_is_directory=True)
    elif mutation == "forged-credit":
        data["rows"][0]["results"]["reviewed"]["total_credit"] = 0
    elif mutation == "operational":
        data["operational_controls"]["helper-failure"]["total_credit"] = 0
    elif mutation == "stale-source":
        (tmp_path / "inputs/host/scorer_native.py").write_text("# stale\n")
    elif mutation == "selected-arm":
        selected["scorer_controls"]["arm"] = "unreviewed"
    elif mutation == "selected-root":
        selected["artifacts"][0]["path"] = "panel/submissions.json"
    elif mutation == "summary-only":
        (trial / "result.json").unlink()
        (tmp_path / "scorer-native-summary.json").write_text('{"status":"passed","score":3}')
    if mutation in {"missing", "extra", "forged-credit", "operational"}:
        path.write_text(json.dumps(data))
    result, report = cli(tmp_path, selected)
    assert result.returncode == 2
    assert report["status"] == "invalid"
    assert "items" not in report
    replay = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(tmp_path),
            "--selection",
            str(tmp_path / "rubric-selection.json"),
            "--verify-existing",
            str(retained_report),
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert replay.returncode == 2
    assert json.loads(replay.stdout)["status"] == "invalid"


def test_forged_measurement_and_late_arm_change_are_not_authority(tmp_path):
    output = tmp_path / "fixture"
    retained = runner.prepare(output)
    (output / "panel.json").write_text(runner.encoded(runner.run_panel(output, retained)))
    module = measure_module()
    selection = selection_for(output, "reviewed")
    report = module.measure(output, selection)
    report["measurements"]["scorer_controls"]["native"] = {"status": "passed"}
    with pytest.raises(ValueError):
        module.assess(report["measurements"], selection, root=output)
    report = module.measure(output, selection)
    selection["scorer_controls"]["arm"] = "baseline"
    with pytest.raises(ValueError):
        module.build_report(report["items"], report["measurements"], selection, output)


def test_measure_verify_existing_revalidates_native_receipts(tmp_path):
    _, trial = synthetic_job(tmp_path)
    selected = selection_for(tmp_path, "reviewed", "native")
    _, report = cli(tmp_path, selected)
    retained_report = tmp_path / "rubric-reviewed.json"
    retained_report.write_text(json.dumps(report))
    command = [
        sys.executable,
        str(SCRIPT),
        "--root",
        str(tmp_path),
        "--selection",
        str(tmp_path / "rubric-selection.json"),
        "--verify-existing",
        str(retained_report),
    ]
    replay = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert replay.returncode in (0, 1), replay.stderr
    assert json.loads(replay.stdout) == report
    (trial / "verifier/reward.txt").write_text("0")
    replay = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert replay.returncode == 2
    assert json.loads(replay.stdout)["status"] == "invalid"


@pytest.mark.parametrize(
    "field,value", [("score", 3), ("native_passed", True), ("arm", None), ("mode", []), ("evidence_root", "../other")]
)
def test_caller_fields_cannot_confer_rubric_merit(tmp_path, field, value):
    output = tmp_path / "fixture"
    selected = runner.prepare(output)
    (output / "panel.json").write_text(runner.encoded(runner.run_panel(output, selected)))
    selected = selection_for(output, "reviewed")
    selected["scorer_controls"][field] = value
    result, report = cli(output, selected)
    assert result.returncode == 2
    assert report["status"] == "invalid"


@pytest.mark.parametrize("mutation", ["duplicate-json", "missing", "extra", "arm", "symlink"])
def test_fixture_tamper_rejected_by_run_and_verify(tmp_path, mutation):
    output = tmp_path / "fixture"
    selected = runner.prepare(output)
    (output / "panel.json").write_text(runner.encoded(runner.run_panel(output, selected)))
    selected = selection_for(output, "reviewed")
    _, report = cli(output, selected)
    retained_report = output / "rubric-reviewed.json"
    retained_report.write_text(json.dumps(report))
    path = output / "panel.json"
    panel = json.loads(path.read_text())
    if mutation == "missing":
        panel["cases"].pop()
    elif mutation == "extra":
        panel["cases"].append(panel["cases"][0])
    elif mutation == "arm":
        panel["cases"][0]["observed"]["reviewed"] = panel["cases"][0]["observed"]["baseline"]
    elif mutation == "duplicate-json":
        path.write_text(path.read_text().replace("{", '{"status":"passed",', 1))
    else:
        path.rename(output / "moved.json")
        path.symlink_to(output / "moved.json")
    if mutation in {"missing", "extra", "arm"}:
        path.write_text(json.dumps(panel))
    result, report = cli(output, selected)
    assert result.returncode == 2
    assert report["status"] == "invalid"
    replay = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(output),
            "--selection",
            str(output / "rubric-selection.json"),
            "--verify-existing",
            str(retained_report),
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert replay.returncode == 2
    assert json.loads(replay.stdout)["status"] == "invalid"


def measure_module():
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("refund_rubric_measure", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def selection_for(output, arm, mode="fixture"):
    selected = json.loads((output / "selection.json").read_text())
    for record in selected["artifacts"]:
        record["path"] = "inputs/" + record["path"]
        for evidence in record["evidence"]:
            evidence["path"] = "inputs/" + evidence["path"]
    selected["scorer_controls"] = {"evidence_root": ".", "mode": mode, "arm": arm}
    return selected


def cli(output, selected):
    path = output / "rubric-selection.json"
    path.write_text(json.dumps(selected))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(output), "--selection", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.stderr == ""
    return result, json.loads(result.stdout)


def test_fixture_controls_reach_canonical_scores_through_real_cli(tmp_path):
    selection = runner.prepare(tmp_path / "fixture")
    output = tmp_path / "fixture"
    panel = runner.run_panel(output, selection)
    (output / "panel.json").write_text(runner.encoded(panel))
    reports = {}
    for arm, score in (("baseline", 0), ("reviewed", 1)):
        selected = selection_for(output, arm)
        result, report = cli(output, selected)
        assert result.returncode in (0, 1)
        assert "scorer_controls" in report["measurements"], "selected controls were ignored"
        item = report["items"]["AC-02"]
        assert item["score"] == score
        assert item["evidence_strength"] == "E2"
        assert item["definition_ref"] == "anti-cheat.md#ac-02-shortcuts-do-not-earn-forbidden-credit"
        assert {c["method"] for c in item["check_results"]} == {"review", "probe", "deterministic"}
        assert all("check_id" not in c for c in item["check_results"])
        assert item["remaining_gaps"]
        assert report["items"]["DISC-01-B"]["score"] == 2
        assert all(report["items"][ident]["score"] is None for ident in ("AC-01", "AC-03", "AC-04"))
        assert report["anti_cheat_scores"] == {"AC-01": None, "AC-02": score, "AC-03": None, "AC-04": None}
        assert cli(output, selected)[1] == report
        reports[arm] = report
    assert (
        reports["baseline"]["items"]["AC-02"]["subject_revision"]
        != reports["reviewed"]["items"]["AC-02"]["subject_revision"]
    )
    baseline_controls = reports["baseline"]["measurements"]["scorer_controls"]["controls"]
    assert next(row for row in baseline_controls if row["id"] == "missing-completion")["defense_pass"] is False
    observed = reports["reviewed"]["measurements"]["scorer_controls"]
    assert observed["counts"] == {"applicable": 18, "healthy": 18, "intended": 18, "operational": 3}
    assert set(observed["attack_class_counts"]) == set(observed["applicability"]["attack_classes"])
    assert all(
        row["healthy"] == row["applicable"] == row["intended"] for row in observed["attack_class_counts"].values()
    )
    assert observed["required_valid_controls_passed"] is True
    wrong_amount = next(row for row in observed["controls"] if row["id"] == "wrong-amount")
    assert wrong_amount["observed"]["raw_credit"] == 8
    assert wrong_amount["observed"]["total_credit"] == 0
    assert wrong_amount["forbidden_credit"] is False
    assert all(row["defense_pass"] is None for row in observed["controls"] if row["role"] == "operational")
