# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/eval-author-rubric"
SUITE = SKILL / "examples/finish-suite"
FAMILIES = ("interval-reservation", "dependency-release")


def load(path):
    spec = importlib.util.spec_from_file_location("finish_test_" + path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("family", FAMILIES)
def test_full_template_actual_scoring_and_optional_credit(family):
    task = SUITE / family
    assert (task / "tests/verify.py").is_file(), "full Harbor template missing"
    scorer = load(task / "tests/verify.py")
    reference = load(task / "solution/reference.py")
    case = json.loads((task / "environment/case.json").read_text())
    candidate = reference.solve(case)
    result = scorer.evaluate(case, candidate)
    assert result["health"] == "ok"
    assert result["total_credit"] == result["max_credit"] == 3
    assert result["checks"] == {"well_formed_choice": 1, "current_evidence_correct": 1, "optional_completion": 1}
    assert scorer.evaluate(case, {**candidate, "success": False})["total_credit"] == 2
    wrong = next(o["id"] for o in case["options"] if o["id"] != candidate["choice"])
    invalid = scorer.evaluate(case, {"choice": wrong, "success": True})
    assert invalid["checks"]["optional_completion"] == 1
    assert invalid["raw_credit"] == 2
    assert invalid["total_credit"] == 0


def test_policy_catalog_executes_real_work_without_scorer(tmp_path):
    path = SKILL / "current_rubric_finish_policies.py"
    assert path.exists(), "deterministic policy catalog missing"
    policies = load(path)
    for family in (*FAMILIES, "refund"):
        source = (
            SUITE / family / "environment/case.json"
            if family != "refund"
            else SKILL / "examples/refund/base/environment/contract.json"
        )
        result = policies.run_policy("evidence", family, source.read_bytes())
        assert result["reads"][0]["sha256"]
        assert result["actions"]
        assert result["execution"] == "planned_not_submitted"
        if family == "refund":
            scorer = load(SKILL / "examples/refund/base/tests/refund_scorer.py")
            scored = scorer.evaluate(json.loads(source.read_bytes()), result["candidate"])
        else:
            scorer = load(SUITE / family / "tests/verify.py")
            scored = scorer.evaluate(json.loads(source.read_bytes()), result["candidate"])
        assert scored["total_credit"] == scored["max_credit"]
        changed = json.loads(source.read_bytes())
        if family != "refund":
            changed["options"].reverse()
            first = policies.run_policy("first-template", family, source.read_bytes())
            flipped = policies.run_policy("first-template", family, json.dumps(changed).encode())
            assert first["candidate"] != flipped["candidate"]
        assert "verify" not in path.read_text() and "scorer" not in path.read_text()


@pytest.mark.parametrize("family", FAMILIES)
def test_schema_boundaries_and_unsafe_candidates(family, tmp_path):
    import os
    import tomllib

    from harbor.models.task.config import TaskConfig
    from harbor.models.task.task import Task
    from pydantic import ValidationError

    task = SUITE / family
    assert Task.is_valid_dir(task)
    raw = tomllib.loads((task / "task.toml").read_text())
    parsed = TaskConfig.model_validate(raw)
    assert parsed.agent.user == "65534" and parsed.verifier.user == "root"
    assert parsed.agent.timeout_sec is not None and parsed.agent.timeout_sec <= 30
    raw["environment"]["cpus"] = "invalid-type"
    with pytest.raises(ValidationError):
        TaskConfig.model_validate(raw)
    scorer = load(task / "tests/verify.py")
    case = json.loads((task / "environment/case.json").read_bytes())
    candidate = load(task / "solution/reference.py").solve(case)
    for bad in (
        {},
        {"choice": True, "success": True},
        {**candidate, "extra": 1},
        {**candidate, "success": 1},
        {"choice": "unlisted", "success": True},
    ):
        assert scorer.evaluate(case, bad)["total_credit"] == 0
    evidence = "occupied" if family == FAMILIES[0] else "states"
    case[evidence] = None
    assert scorer.evaluate(case, {"choice": None, "success": True})["total_credit"] == 3
    candidate_path = tmp_path / "candidate.json"
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(case))
    candidate_path.write_text('{"choice": null, "choice": null, "success": true}')
    assert scorer.grade_paths(case_path, candidate_path)["total_credit"] == 0
    candidate_path.unlink()
    assert scorer.grade_paths(case_path, candidate_path)["total_credit"] == 0
    candidate_path.symlink_to(case_path)
    assert scorer.grade_paths(case_path, candidate_path)["health"] == "error"
    candidate_path.unlink()
    os.mkfifo(candidate_path)
    assert scorer.grade_paths(case_path, candidate_path)["health"] == "error"
    candidate_path.unlink()
    os.link(case_path, candidate_path)
    assert scorer.grade_paths(case_path, candidate_path)["health"] == "error"
    candidate_path.unlink()
    candidate_path.write_bytes(b"x" * 65537)
    assert scorer.grade_paths(case_path, candidate_path)["health"] == "error"


def test_prepare_offline_manifest_and_selection(tmp_path):
    path = SKILL / "current_rubric_finish_native.py"
    assert path.exists(), "offline finish preparation missing"
    native = load(path)
    result = native.prepare(tmp_path / "prepared")
    assert result["native_execution"] is False
    assert result["authorization"] is None
    assert result["confirmation"]["status"] == "awaiting_independent_custody"
    assert result["confirmation"]["cases"] == []
    assert len(result["episodes"]) == 8
    assert all(e["status"] == "not_run" for e in result["episodes"])
    assert result["selection"]["selected"] in native.POLICIES
    assert len(result["development"]) == 18
    assert native.validate_prepared(tmp_path / "prepared") == result
    (tmp_path / "prepared/tasks/interval-reservation/environment/case.json").write_text("{}")
    with pytest.raises(ValueError, match="binding"):
        native.validate_prepared(tmp_path / "prepared")


@pytest.mark.parametrize("family", FAMILIES)
def test_malformed_case_is_health_error(family):
    scorer = load(SUITE / family / "tests/verify.py")
    original = json.loads((SUITE / family / "environment/case.json").read_bytes())
    for options in ([1], [None], [{"id": "x"}], [{"id": []}], []):
        result = scorer.evaluate({**original, "options": options}, {"choice": None, "success": True})
        assert result["health"] == "error" and result["total_credit"] is None


def test_actual_policy_file_and_action_socket(tmp_path):
    import socket
    import threading

    policies = load(SKILL / "current_rubric_finish_policies.py")
    source = SUITE / FAMILIES[0] / "environment/case.json"
    candidate = tmp_path / "candidate.json"
    trace = policies.execute_policy("evidence", FAMILIES[0], source, candidate_path=candidate)
    assert trace["execution"] == "submitted"
    assert json.loads(candidate.read_bytes()) == trace["candidate"]
    received = []
    # macOS AF_UNIX path length is bounded; scratch's parent path is too long under pytest.
    import tempfile

    with tempfile.TemporaryDirectory(dir=str(Path("/tmp").resolve()), prefix="fs-") as short:
        socket_path = Path(short) / "s"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(socket_path))
            server.listen()
            server.settimeout(3)
            source = SKILL / "examples/refund/base/environment/contract.json"
            planned = policies.run_policy("evidence", "refund", source.read_bytes())

            def serve():
                for _ in planned["actions"]:
                    connection, _ = server.accept()
                    with connection:
                        received.append(json.loads(connection.makefile("rb").readline()))
                        connection.sendall(b'{"ok": true}\n')

            worker = threading.Thread(target=serve)
            worker.start()
            trace = policies.execute_policy("evidence", "refund", source, socket_path=socket_path)
            worker.join(timeout=4)
            assert not worker.is_alive()
    assert received == planned["actions"]
    assert trace["execution"] == "submitted"
    assert len(trace["action_receipts"]) == len(received)


def test_plan_contains_exact_candidates_and_measured_selection_changes(tmp_path):
    import copy

    native = load(SKILL / "current_rubric_finish_native.py")
    result = native.prepare(tmp_path / "prepared")
    for episode in result["episodes"]:
        assert "candidate" in episode
        assert "candidate_sha256" in episode
        assert episode["consumer"] == "/logs/verifier/reward.txt"
    feedback = copy.deepcopy(result["development"])
    for row in feedback:
        row["result"]["accepted"] = row["policy"] == "lexical"
    assert native.select_policy(feedback)["selected"] == "lexical"
    with pytest.raises(ValueError, match="incomplete"):
        native.select_policy(feedback[:-1])
    path = tmp_path / "prepared/manifest.json"
    changed = json.loads(path.read_bytes())
    changed["selection"]["selected"] = "lexical"
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="binding"):
        native.validate_prepared(tmp_path / "prepared")


@pytest.mark.parametrize("family", FAMILIES)
def test_materialized_mutant_is_out_of_place_and_real_gate_omission(family, tmp_path):
    native = load(SKILL / "current_rubric_finish_native.py")
    original = (SUITE / family / "tests/verify.py").read_bytes()
    target = tmp_path / "mutant"
    metadata = native.materialize(family, target, grader="completion-only")
    assert metadata["scope"] == "cohort/completion-only"
    scorer = load(target / "tests/verify.py")
    case = json.loads((target / "environment/case.json").read_bytes())
    result = scorer.evaluate(case, {"choice": None, "success": True})
    assert result["checks"]["current_evidence_correct"] == 0
    assert result["total_credit"] == 3
    assert result["accepted"] is False
    assert (SUITE / family / "tests/verify.py").read_bytes() == original


def test_development_covers_variations_not_only_original(tmp_path):
    native = load(SKILL / "current_rubric_finish_native.py")
    document = native.prepare(tmp_path / "prepared")
    rows = document["development"]
    assert len(rows) == 18
    assert len({(r["family"], r["input_id"]) for r in rows}) == 6
    assert all(r["partition"] == "exposed_development" for r in rows)
    assert set(document["selection"]["correct_counts"]) == set(native.POLICIES)


def test_algorithms_agree_on_enumerated_exposed_boundaries():
    import itertools

    scorer = load(SUITE / FAMILIES[0] / "tests/verify.py")
    reference = load(SUITE / FAMILIES[0] / "solution/reference.py")
    intervals = [list(pair) for pair in itertools.combinations(range(-2, 4), 2)]
    checked = 0
    for occupied, left, right in itertools.product(intervals, repeat=3):
        case = {
            "id": "enumerated-development",
            "occupied": [occupied],
            "options": [{"id": "left", "interval": left}, {"id": "right", "interval": right}],
        }
        try:
            candidate = reference.solve(case)
        except ValueError:
            assert scorer.evaluate(case, {"choice": None, "success": True})["health"] == "error"
        else:
            assert scorer.evaluate(case, candidate)["total_credit"] == 3
            checked += 1
    assert checked > 0
    scorer = load(SUITE / FAMILIES[1] / "tests/verify.py")
    reference = load(SUITE / FAMILIES[1] / "solution/reference.py")
    for a, b in itertools.product(("passed", "revoked", "pending", "failed", None), repeat=2):
        states = {key: value for key, value in (("a", a), ("b", b)) if value is not None}
        case = {
            "id": "release-enumerated-development",
            "states": states,
            "options": [{"id": "empty", "requires": []}, {"id": "both", "requires": ["a", "b"]}],
        }
        if a == b == "passed":
            assert scorer.evaluate(case, {"choice": "empty", "success": True})["health"] == "error"
        else:
            assert scorer.evaluate(case, reference.solve(case))["total_credit"] == 3


def test_parent_alias_and_isolated_reference_cli(tmp_path):
    import subprocess
    import sys

    task = SUITE / FAMILIES[0]
    scorer = load(task / "tests/verify.py")
    target = tmp_path / "real"
    target.mkdir()
    (target / "candidate.json").write_text("{}")
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        scorer.read_regular(alias / "candidate.json")
    candidate = tmp_path / "output.json"
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(task / "solution/reference.py"),
            "--case",
            str(task / "environment/case.json"),
            "--candidate",
            str(candidate),
        ],
        capture_output=True,
        timeout=5,
    )
    assert done.returncode == 0, done.stderr
    assert scorer.grade_paths(task / "environment/case.json", candidate)["total_credit"] == 3
