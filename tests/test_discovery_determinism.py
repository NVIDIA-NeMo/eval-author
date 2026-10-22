# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import check_report_facts as factual
import discovery_determinism as determinism
from test_skill_contract import _run_discover, _write_task


def capture(tmp_path, report, index, code=1, **identity):
    path = tmp_path / f"raw-{index}.json"
    path.write_text(json.dumps(report))
    provenance = {key: "frozen-fixture-v1" for key in determinism.IDENTITIES} | identity
    return determinism.capture(path, provenance, code)


@pytest.mark.parametrize("case", ["harbor", "gym", "mixed", "empty", "malformed", "ignored", "dropped", "missing-env"])
def test_actual_discovery_repeats(tmp_path, monkeypatch, case):
    """Run real Explore/Judge with installed Harbor and controlled fake NeMo Gym; no Solve tasks."""
    repo = tmp_path / "repo"
    repo.mkdir()
    if case not in {"gym", "empty"}:
        _write_task(repo / "dataset/a")
        (repo / "job.yaml").write_text("datasets:\n  - path: ./dataset\nagents:\n  - name: oracle\n")
    if case in {"gym", "mixed"}:
        manifest = repo / "environments/demo/manifest.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("name: demo\nkind: environment\ndatasets:\n  - jsonl_fpath: data/test.jsonl\n")
    if case == "malformed":
        (repo / "job.yaml").write_text("datasets: [broken\n")
    if case == "dropped":
        _write_task(repo / "dataset/b")
        (repo / "dataset/b/task.toml").write_text("broken = = =")
    if case == "ignored":
        _write_task(repo / ".git/ignored")
        _write_task(repo / "nested/.git/ignored")
    if case == "missing-env":
        task = repo / "dataset/a/task.toml"
        task.write_text(task.read_text() + '\n[environment.env]\nDETERMINISM_ABSENT = "${DETERMINISM_ABSENT}"\n')
        monkeypatch.delenv("DETERMINISM_ABSENT", raising=False)
    records = []
    for index, cwd in enumerate([tmp_path, repo]):
        monkeypatch.chdir(cwd)
        code, report = _run_discover(repo)
        records.append(capture(tmp_path, report, index, code))
    assert determinism.compare(records)["passed"]
    if case == "mixed":
        assert records[0]["decision"]["gym_manifests"][0]["judge"]["status"] == "blocked"
        assert records[0]["exit_status"] == 1


def minimal():
    return {
        "schema_version": 1,
        "repo_root": "/fixture",
        "runtime": {"version": "1"},
        "phases": {"solve": "skipped"},
        "configs": [],
        "datasets": [],
        "gym_manifests": [],
        "checks": [{"name": "backend", "status": "fail", "severity": "required", "reason": "missing"}],
        "proven": False,
        "runnable": False,
        "task_count": 0,
        "sample": ["a", "c"],
        "discovered_at": "first",
    }


def test_discovery_excludes_internal_configs_and_legacy_copies(tmp_path, monkeypatch):
    from test_skill_contract import _import_discover

    discover = _import_discover(monkeypatch)
    current = tmp_path / ".eval-author/tasks/current"
    _write_task(current)
    for relative in (".working/trace-environments", "task-drafts", "archives"):
        root = tmp_path / ".eval-author" / relative
        _write_task(root / "copy")
        (root / "run.yaml").write_text("tasks:\n  - path: copy\n")
    scan = discover.scan_repository(tmp_path)
    assert scan.configs == []
    assert {item["path"] for item in scan.task_search["candidates"]} == {".eval-author/tasks/current"}
    selected = discover.scan_repository(tmp_path / ".eval-author/task-drafts")
    assert [item.path.name for item in selected.configs] == ["run.yaml"]
    assert {item["path"] for item in selected.task_search["candidates"]} == {"copy"}


def test_normalization_retains_samples_unknown_fields_and_duplicates(tmp_path):
    report = minimal()
    first = capture(tmp_path, report, 0)
    report["discovered_at"] = "second"
    second = capture(tmp_path, report, 1)
    assert determinism.compare([first, second])["passed"]
    for key, value in [
        ("sample", ["c", "a"]),
        ("runtime", {"version": "2"}),
        ("new_field", "not discarded"),
        ("checks", report["checks"] * 2),
    ]:
        changed = copy.deepcopy(report)
        changed[key] = value
        third = capture(tmp_path, changed, 2)
        assert not determinism.compare([first, third])["passed"]
        third = capture(tmp_path, changed, 2, fixture="changed")
        assert determinism.compare([first, third], mutation=True)["passed"]
    with pytest.raises(ValueError, match="at least two"):
        determinism.compare([first])
    Path(first["raw_report"]).write_text("{}")
    with pytest.raises(ValueError):
        determinism.compare([first, second])


def test_copied_discovery_report_does_not_count_as_another_attempt(tmp_path):
    first = capture(tmp_path, minimal(), 0)
    copied = capture(tmp_path, minimal(), 1)
    with pytest.raises(ValueError, match="copied reports"):
        determinism.compare([first, copied])


@pytest.mark.parametrize("mutation", ["omission", "contradiction", "unsupported", "stage", "source", "boolean"])
def test_factual_summary_rejects_changed_claims(tmp_path, mutation):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(minimal()))
    candidate = factual.ledger(source, "validation")
    assert factual.check(source, candidate, "validation")["passed"]
    assert factual.render(candidate) == factual.render(factual.ledger(source, "validation"))
    if mutation == "omission":
        del candidate["claims"]["/checks/0/status"]
    elif mutation == "contradiction":
        candidate["claims"]["/checks/0/status"] = "pass"
    elif mutation == "unsupported":
        candidate["claims"]["/all_tasks_passed"] = True
    elif mutation == "stage":
        candidate["stage"] = "acceptance"
    elif mutation == "source":
        source.write_text(source.read_text() + "\n")
    else:
        candidate["claims"]["/task_count"] = False
    assert not factual.check(source, candidate, "validation")["passed"]


def test_same_capture_cannot_be_counted_twice(tmp_path):
    record = capture(tmp_path, minimal(), 0)
    with pytest.raises(ValueError, match="distinct"):
        determinism.compare([record, record])


def test_unknown_nested_collections_are_not_normalized():
    first = minimal() | {"extension": {"datasets": ["a", "b"]}}
    second = minimal() | {"extension": {"datasets": ["b", "a"]}}
    assert determinism.projection(first) != determinism.projection(second)


def test_free_prose_cannot_hide_outside_checked_claims(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(minimal()))
    candidate = factual.ledger(source, "validation") | {"summary": "All tasks passed."}
    assert not factual.check(source, candidate, "validation")["passed"]


def test_discovery_sample_survives_creation_and_concurrent_completion_order(tmp_path, monkeypatch, capsys):
    import asyncio
    import shutil
    import time

    from test_skill_contract import _import_discover

    discover = _import_discover(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "data.jsonl").write_text('{"row": 1}\n')
    names = list("abcdefg")
    monkeypatch.setattr(discover._probe, "probe", lambda root: {"harbor_cli": None})
    monkeypatch.setattr(discover._probe, "is_available", lambda runtime: False)
    monkeypatch.setattr(discover._probe, "probe_checks", lambda runtime: [])
    monkeypatch.setattr(discover.gym_probe, "probe", lambda root, cli: {"gym_cli": "fixture", "gym_available": True})
    monkeypatch.setattr(discover.gym_probe, "probe_checks", lambda *args, **kwargs: [])
    completed = []
    reverse = False

    def validate(cli, name, kind, root):
        index = names.index(name)
        time.sleep(0.01 * (6 - index if reverse else index))
        completed.append(name)
        return discover.gym_judge.GymRun(error="native-rejected" if name == "d" else None)

    monkeypatch.setattr(discover.gym_judge, "validate", validate)
    monkeypatch.setattr(
        discover.gym_solve, "run_fixture", lambda *args: discover.gym_judge.GymRun(report={"cases": []})
    )
    records, completion_orders = [], []
    for index, order in enumerate([names, list(reversed(names))]):
        reverse = bool(index)
        for name in order:
            folder = repo / "environments" / name
            folder.mkdir(parents=True)
            (folder / "manifest.yaml").write_text(
                f"name: {name}\nkind: environment\ndatasets:\n  - jsonl_fpath: data.jsonl\n"
            )
        monkeypatch.chdir(repo if index else tmp_path)
        code = asyncio.run(discover._main(["--repo", str(repo)]))
        report = json.loads(capsys.readouterr().out)
        records.append(capture(tmp_path, report, index, code))
        completion_orders.append(completed[:])
        completed.clear()
        if index == 0:
            shutil.rmtree(repo / "environments")
    assert completion_orders[0] != completion_orders[1]
    assert determinism.compare(records)["passed"]
    entries = records[0]["decision"]["gym_manifests"]
    assert [entry["name"] for entry in entries if entry["solve"]["status"] == "passed"] == ["a", "c", "e", "g"]
    assert sum(entry["solve"]["status"] == "not sampled" for entry in entries) == 2
    assert records[0]["decision"]["runnable"] is False  # good samples cannot hide rejected d
    assert records[0]["decision"]["phases"]["judge"] == "invalid"

    (repo / "environments/a/manifest.yaml").write_text(
        "name: a\nkind: environment\ndatasets:\n  - jsonl_fpath: absent.jsonl\n"
    )
    code = asyncio.run(discover._main(["--repo", str(repo)]))
    changed = capture(tmp_path, json.loads(capsys.readouterr().out), 2, code, fixture="changed-config")
    assert determinism.compare([records[0], changed], mutation=True)["passed"]
