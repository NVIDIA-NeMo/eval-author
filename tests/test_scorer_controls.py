# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def runner():
    path = ROOT / "tools/run_scorer_controls.py"
    assert path.is_file(), "scorer regression entry point is missing"
    spec = importlib.util.spec_from_file_location("scorer_controls_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_identity_cli_rejects_changed_scorer_preserves_neighbor(tmp_path):
    module = runner()
    (tmp_path / "scorer.py").write_text("trusted = True\n")
    (tmp_path / "contract.json").write_text("{}\n")
    selection = module.select_inputs(tmp_path, {"scorer": "scorer.py", "contract": "contract.json"})
    before = module.measure_identity(tmp_path, selection)
    assert before["measurements"]["identity"]["status"] == "pass"
    assert before["measurements"]["route"]["bound_dependency"] is True
    (tmp_path / "scorer.py").write_text("trusted = False\n")
    after = module.measure_identity(tmp_path, selection)
    diagnostics = after["measurements"]["identity"]["diagnostics"]
    assert {row["subject"] for row in diagnostics} == {"scorer"}
    assert {row["code"] for row in diagnostics} == {"identity.digest_mismatch"}
    assert after["measurements"]["route"]["status"] != "pass"


def test_fixture_command_is_repeatable_and_replays_retained_bytes(tmp_path):
    command = [sys.executable, str(ROOT / "tools/run_scorer_controls.py")]
    outputs = []
    for name in ("first", "second"):
        result = subprocess.run(
            command + ["--output", str(tmp_path / name)], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stderr
        outputs.append(json.loads(result.stdout))
    assert outputs[0] == outputs[1]
    assert outputs[0]["status"] == "passed"
    assert outputs[0]["execution"] == "fixture-only"
    assert outputs[0]["task_qualification"] == "NOT_ASSESSED"
    assert outputs[0]["native_execution"] is False
    report = tmp_path / "first/panel.json"
    assert json.loads(report.read_text()) == outputs[0]
    replay = subprocess.run(
        command + ["--verify-existing", str(tmp_path / "first")], capture_output=True, text=True, timeout=60
    )
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout) == outputs[0]
    (tmp_path / "first/inputs/contract.json").write_text("{}\n")
    tampered = subprocess.run(
        command + ["--verify-existing", str(tmp_path / "first")], capture_output=True, text=True, timeout=60
    )
    assert tampered.returncode != 0
    assert json.loads(tampered.stdout)["status"] == "invalid"


@pytest.mark.parametrize("mode", ["--output", "--verify-existing"])
def test_fixture_rubric_mismatch_fails_with_passing_controls(tmp_path, monkeypatch, capsys, mode):
    module = runner()
    output = tmp_path / "retained"
    real_measure = module.measure_identity

    def mismatched_measure(root, selected):
        report = real_measure(root, selected)
        if selected.get("scorer_controls", {}).get("arm") == "reviewed":
            report["items"]["AC-02"]["score"] = 0
        return report

    monkeypatch.setattr(module, "measure_identity", mismatched_measure)
    if mode == "--verify-existing":
        assert module.main(["--output", str(output), "--rubric"]) == 1
        capsys.readouterr()
    before = {path: path.read_bytes() for path in output.rglob("*") if path.is_file()}
    result = module.main([mode, str(output), "--rubric"])
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["rubric_comparison"] == {
        "criterion": "AC-02",
        "expected": {"baseline": 0, "reviewed": 1},
        "observed": {"baseline": 0, "reviewed": 0},
        "matched": False,
    }
    assert report["matched_count"] == report["control_count"] == 21
    assert all(row["matched"] for row in report["cases"])
    assert report["status"] == "failed"
    assert result == 1
    assert captured.err == ""
    assert json.loads((output / "rubric-reviewed.json").read_text())["items"]["AC-02"]["score"] == 0
    if mode == "--verify-existing":
        assert {path: path.read_bytes() for path in output.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("selection", None),
        ("selection", []),
        ("selection", 0),
        ("selection", "bad"),
        ("selection", True),
        ("selection", 1.5),
        ("artifacts", None),
        ("artifacts", {}),
        ("artifacts", 0),
        ("artifacts", "bad"),
        ("artifacts", []),
        ("record", None),
        ("record", []),
        ("record", 0),
        ("record", "bad"),
        ("record", {}),
        ("id", None),
        ("id", []),
        ("path", None),
        ("path", []),
        ("digest", None),
        ("digest", []),
    ],
)
def test_replay_cli_rejects_malformed_selection(tmp_path, target, value):
    module = runner()
    output = tmp_path / "retained"
    selection = module.prepare(output)
    if target == "selection":
        selection = value
    elif target == "artifacts":
        selection[target] = value
    elif target == "record":
        selection["artifacts"][0] = value
    else:
        selection["artifacts"][0][target] = value
    (output / "selection.json").write_text(module.encoded(selection))
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/run_scorer_controls.py"), "--verify-existing", str(output)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout) == {
        "schema": "refund-scorer-panel-v1",
        "status": "invalid",
        "reason": "input_or_execution_invalid",
    }
    assert result.stderr == ""
    # Reject the input before looking for a retained report.
    # A missing report alone must not cause this rejection.
    with pytest.raises(ValueError):
        module.run_panel(output, selection)


def test_replay_rejects_coherent_report_credit_edit(tmp_path):
    module = runner()
    selection = module.prepare(tmp_path / "retained")
    report = module.run_panel(tmp_path / "retained", selection)
    report["cases"][0]["observed"]["reviewed"]["total_credit"] = 0
    (tmp_path / "retained/panel.json").write_text(module.encoded(report))
    assert module.main(["--verify-existing", str(tmp_path / "retained")]) == 2


def test_replay_rejects_omitted_selected_input(tmp_path):
    module = runner()
    selection = module.prepare(tmp_path / "retained")
    selection["artifacts"] = [r for r in selection["artifacts"] if r["id"] != "controls"]
    selection["route"]["dependencies"]["scorer"].remove("controls")
    del selection["route"]["dependencies"]["controls"]
    dispatch = tmp_path / "retained/inputs/dispatch.json"
    value = json.loads(dispatch.read_text())
    value["dependencies"] = selection["route"]["dependencies"]
    dispatch.write_text(module.encoded(value))
    import hashlib

    for record in selection["artifacts"]:
        if record["id"] == "dispatch":
            record["digest"] = hashlib.sha256(dispatch.read_bytes()).hexdigest()
            record["evidence"][0]["digest"] = record["digest"]
    import pytest

    with pytest.raises(ValueError, match="selection"):
        module.run_panel(tmp_path / "retained", selection)


def reseal(module, output, selection, ident):
    record = next(row for row in selection["artifacts"] if row["id"] == ident)
    digest = hashlib.sha256((output / "inputs" / record["path"]).read_bytes()).hexdigest()
    record["digest"] = record["revision"] = record["evidence"][0]["digest"] = digest
    (output / "selection.json").write_text(module.encoded(selection))


@pytest.mark.parametrize(
    ("relative", "ident", "field", "value"),
    [
        ("panel.json", None, "status", "failed"),
        ("selection.json", None, "artifacts", []),
        ("inputs/cases.json", "controls", "schema", "wrong"),
        ("inputs/runtime.json", "runtime", "execution", "native"),
    ],
)
def test_replay_cli_rejects_duplicate_json_keys(tmp_path, relative, ident, field, value):
    module = runner()
    output = tmp_path / "retained"
    selection = module.prepare(output)
    target = output / relative
    if ident is None:
        report = module.run_panel(output, selection)
        (output / "panel.json").write_text(module.encoded(report))
    target.write_text(target.read_text().replace("{", "{" + json.dumps(field) + ":" + json.dumps(value) + ",", 1))
    if ident is not None:
        reseal(module, output, selection, ident)
        # Production replay must reject the input before reading a report.
        # This test needs no forged report.
        with pytest.raises(ValueError, match="duplicate JSON key"):
            module.run_panel(output, selection)
    else:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/run_scorer_controls.py"), "--verify-existing", str(output)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 2, json.loads(result.stdout)["status"]
        assert json.loads(result.stdout)["status"] == "invalid"


@pytest.mark.parametrize("mutation", ["subset", "role", "schema", "expected", "contract"])
def test_panel_rejects_self_resealed_fixed_fixture_edits(tmp_path, mutation):
    module = runner()
    output = tmp_path / "retained"
    selection = module.prepare(output)
    ident = "contract" if mutation == "contract" else "controls"
    path = output / "inputs" / ("contract.json" if ident == "contract" else "cases.json")
    document = json.loads(path.read_bytes())
    if mutation == "subset":
        document["cases"] = [document["cases"][0]]
    elif mutation == "role":
        document["cases"][0]["role"] = "invalid"
    elif mutation == "schema":
        document["schema"] = "wrong-schema"
    elif mutation == "expected":
        document["cases"][0]["expected"]["reviewed"]["accepted"] = False
    else:
        document["requirements"]["amount"] = "Refund anything."
    path.write_text(module.encoded(document))
    reseal(module, output, selection, ident)
    with pytest.raises(ValueError, match="fixed panel"):
        module.run_panel(output, selection)


def test_panel_executes_only_measured_trusted_scorer_bytes(tmp_path, monkeypatch):
    module = runner()
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    for relative in ("contract.json", "cases.json", "refund_scorer.py", "applicability.json"):
        (trusted / relative).write_bytes((module.FIXTURES / relative).read_bytes())
    monkeypatch.setattr(module, "FIXTURES", trusted)
    output = tmp_path / "retained"
    selection = module.prepare(output)
    real_read = module.source_bytes
    scorer_path = trusted / "refund_scorer.py"
    original = scorer_path.read_bytes()

    def replace_after_read(path, **kwargs):
        data = real_read(path, **kwargs)
        if path == scorer_path:
            path.write_text("raise RuntimeError('unmeasured scorer executed')\n")
        return data

    monkeypatch.setattr(module, "source_bytes", replace_after_read)
    try:
        report = module.run_panel(output, selection)
        assert report["status"] == "passed"
        assert report["control_count"] == 21
    finally:
        scorer_path.write_bytes(original)


@pytest.mark.parametrize("relative", ["cases.json", "contract.json", "runtime.json"])
def test_panel_rejects_transient_bytes_between_identity_checks(tmp_path, monkeypatch, relative):
    module = runner()
    output = tmp_path / "retained"
    selection = module.prepare(output)
    target = output / "inputs" / relative
    original = target.read_bytes()
    changed = original + b" "
    if relative == "cases.json":
        document = json.loads(original)
        document["cases"] = [document["cases"][0]]
        document["cases"][0]["id"] = "not-in-selected-controls"
        changed = module.encoded(document).encode()
    real_measure = module.measure_identity
    calls = 0

    def transient(root, selected):
        nonlocal calls
        calls += 1
        if calls == 2:
            target.write_bytes(original)
        result = real_measure(root, selected)
        if calls == 1:
            target.write_bytes(changed)
        return result

    monkeypatch.setattr(module, "measure_identity", transient)
    try:
        with pytest.raises(ValueError, match="selected input digest"):
            module.run_panel(output, selection)
    finally:
        target.write_bytes(original)
    assert target.read_bytes() == original


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("relative", ["panel.json", "selection.json", "inputs/cases.json", "inputs/runtime.json"])
def test_replay_cli_rejects_nonfinite_json(tmp_path, relative, token):
    module = runner()
    output = tmp_path / "retained"
    selection = module.prepare(output)
    report = module.run_panel(output, selection)
    (output / "panel.json").write_text(module.encoded(report))
    target = output / relative
    target.write_text(target.read_text().replace("{", '{"untrusted":' + token + ",", 1))
    if relative.startswith("inputs/"):
        reseal(module, output, selection, "controls" if relative.endswith("cases.json") else "runtime")
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/run_scorer_controls.py"), "--verify-existing", str(output)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "invalid"


def test_panel_rejects_input_root_symlink_replacement_after_measurement(tmp_path, monkeypatch):
    module = runner()
    output = tmp_path / "retained"
    selection = module.prepare(output)
    inputs = output / "inputs"
    moved = output / "moved"
    real_measure = module.measure_identity
    calls = 0

    def replace_root(root, selected):
        nonlocal calls
        calls += 1
        if calls == 2:
            inputs.unlink()
            moved.rename(inputs)
        result = real_measure(root, selected)
        if calls == 1:
            inputs.rename(moved)
            inputs.symlink_to(moved, target_is_directory=True)
        return result

    monkeypatch.setattr(module, "measure_identity", replace_root)
    try:
        with pytest.raises(ValueError, match="selected input unavailable"):
            module.run_panel(output, selection)
    finally:
        if inputs.is_symlink():
            inputs.unlink()
            moved.rename(inputs)


def native_shaped_evidence(output, monkeypatch):
    # Reuse native parser fixtures without leaking their sys.path changes.
    with monkeypatch.context() as scoped:
        scoped.setattr(sys, "path", sys.path[:])
        path = ROOT / "tests/test_scorer_native.py"
        spec = importlib.util.spec_from_file_location("demo_native_test_helpers", path)
        assert spec and spec.loader
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        return helper.synthetic_job(output)


def test_native_demo_cli_retains_both_scores_and_replays_without_launch(tmp_path, monkeypatch):
    output = tmp_path / "native"
    native_shaped_evidence(output, monkeypatch)
    original = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    command = [sys.executable, str(ROOT / "tools/run_scorer_controls.py")]
    result = subprocess.run(command + ["--native-evidence", str(output)], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert report["execution"] == "retained-native"
    assert report["rubric_comparison"] == {
        "criterion": "AC-02",
        "expected": {"baseline": 0, "reviewed": 1},
        "observed": {"baseline": 0, "reviewed": 1},
        "matched": True,
    }
    for arm, score in (("baseline", 0), ("reviewed", 1)):
        selected = json.loads((output / f"rubric-selection-{arm}.json").read_text())
        assert selected["scorer_controls"] == {"arm": arm, "mode": "native", "evidence_root": "."}
        measured = json.loads((output / f"rubric-{arm}.json").read_text())
        assert measured["items"]["AC-02"]["score"] == score
        assert measured["items"]["AC-02"]["remaining_gaps"]
        assert measured["measurements"]["scorer_controls"]["native"]["status"] == "passed"
    replay = subprocess.run(
        command + ["--verify-existing", str(output), "--evidence-mode", "native"],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert replay.returncode == 0, (replay.stdout, replay.stderr)
    assert json.loads(replay.stdout) == report
    assert all((output / relative).read_bytes() == data for relative, data in original.items())
    assert not (output / "panel.json").exists()


@pytest.mark.parametrize("flag", ["--output", "--native-evidence"])
def test_evidence_mode_is_only_a_replay_option(tmp_path, flag):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/run_scorer_controls.py"),
            flag,
            str(tmp_path / "evidence"),
            "--evidence-mode",
            "native",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "--evidence-mode requires --verify-existing" in result.stderr
    assert not (tmp_path / "evidence").exists()


def test_native_demo_does_not_partially_write_when_report_exists(tmp_path, monkeypatch):
    output = tmp_path / "native"
    native_shaped_evidence(output, monkeypatch)
    sentinel = output / "rubric-reviewed.json"
    sentinel.write_text("do not overwrite\n")
    assert runner().main(["--native-evidence", str(output)]) == 2
    assert sentinel.read_text() == "do not overwrite\n"
    assert not (output / "rubric-selection-baseline.json").exists()
    assert not (output / "rubric-baseline.json").exists()


@pytest.mark.parametrize("mutation", ["missing-receipt", "duplicate-selection", "nonfinite-selection", "symlink-root"])
def test_native_demo_rejects_invalid_evidence_despite_passing_summary(tmp_path, monkeypatch, mutation):
    output = tmp_path / "native"
    _, trial = native_shaped_evidence(output, monkeypatch)
    (output / "scorer-native-summary.json").write_text('{"status":"passed","score":3}')
    if mutation == "missing-receipt":
        (trial / "result.json").unlink()
    elif mutation in ("duplicate-selection", "nonfinite-selection"):
        path = output / "selection.json"
        prefix = '{"artifacts":[],' if mutation == "duplicate-selection" else '{"unexpected":NaN,'
        path.write_text(path.read_text().replace("{", prefix, 1))
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(output, target_is_directory=True)
        output = alias
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/run_scorer_controls.py"), "--native-evidence", str(output)],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["status"] == "invalid"
    assert result.stderr == ""
    assert not list(output.glob("rubric-*.json"))


@pytest.mark.parametrize("mutation", ["receipt", "report", "selection", "duplicate-report", "symlink-report"])
def test_native_demo_replay_revalidates_receipts_and_retained_rubric(tmp_path, monkeypatch, capsys, mutation):
    output = tmp_path / "native"
    native, trial = native_shaped_evidence(output, monkeypatch)
    monkeypatch.setattr(native.common, "execute", lambda *a, **k: pytest.fail("native launch forbidden"))
    module = runner()
    assert module.main(["--native-evidence", str(output)]) == 0
    if mutation == "receipt":
        (trial / "verifier/reward.txt").write_text("0")
    else:
        path = output / ("rubric-selection-reviewed.json" if mutation == "selection" else "rubric-reviewed.json")
        if mutation == "symlink-report":
            moved = output / "moved-report.json"
            path.rename(moved)
            path.symlink_to(moved)
        elif mutation == "duplicate-report":
            path.write_text(path.read_text().replace("{", '{"items":{},', 1))
        else:
            document = json.loads(path.read_text())
            if mutation == "selection":
                document["scorer_controls"]["arm"] = "baseline"
            else:
                document["items"]["AC-02"]["score"] = 3
            path.write_text(json.dumps(document))
    original = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    capsys.readouterr()
    assert module.main(["--verify-existing", str(output), "--evidence-mode", "native"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "invalid"
    assert all((output / relative).read_bytes() == data for relative, data in original.items())


@pytest.mark.parametrize("flag", ["--output", "--verify-existing"])
def test_native_evidence_is_exclusive_with_existing_modes(tmp_path, flag):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/run_scorer_controls.py"),
            "--native-evidence",
            str(tmp_path),
            flag,
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "not allowed with argument" in result.stderr


def test_replay_rejects_symlinked_input_directory(tmp_path):
    import pytest

    module = runner()
    selection = module.prepare(tmp_path / "retained")
    inputs = tmp_path / "retained/inputs"
    inputs.rename(tmp_path / "outside")
    inputs.symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(ValueError):
        module.run_panel(tmp_path / "retained", selection)
