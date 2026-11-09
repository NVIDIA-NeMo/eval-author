# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author/scripts"
sys.path.insert(0, str(SCRIPTS))


def load_checker():
    path = SCRIPTS / "rubric_identity.py"
    assert path.is_file(), "The shared rubric identity producer is not implemented"
    spec = importlib.util.spec_from_file_location("rubric_identity", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_selected_artifact_identity_is_measured_without_discovery(tmp_path):
    from eval_author_shared.task_evidence import digest

    checker = load_checker()
    selected = tmp_path / "task.json"
    selected.write_text('{"task_id":"task-1"}')
    record = {
        "id": "task-1",
        "role": "task",
        "path": "task.json",
        "digest": digest(selected),
        "revision": None,
        "access": "available",
        "selection_origin": "explicit",
        "source": "user:turn-1",
        "evidence": [{"path": "task.json", "digest": digest(selected)}],
    }
    result = checker.identify(tmp_path, [record])
    assert result["status"] == "pass"
    assert result["records"] == [record]
    assert result["diagnostics"] == []
    assert result["records"][0]["revision"] is None


@pytest.mark.parametrize("access", ["missing", "denied", "unresolved"])
def test_unavailable_artifacts_never_pass(tmp_path, access):
    checker = load_checker()
    record = {
        "id": "task-1",
        "role": "task",
        "path": "task.json",
        "digest": None,
        "revision": None,
        "access": access,
        "selection_origin": "explicit",
        "source": "user:turn-1",
        "evidence": [],
    }
    result = checker.identify(tmp_path, [record])
    assert result["status"] == "unknown"
    assert result["diagnostics"] == [{"code": "identity.access_" + access, "subject": "task-1", "status": "unknown"}]


def test_invalid_evidence_digest_does_not_pass(tmp_path):
    checker = load_checker()
    path = tmp_path / "task.json"
    path.write_text("{}")
    from eval_author_shared.task_evidence import digest

    record = {
        "id": "task-1",
        "role": "task",
        "path": "task.json",
        "digest": digest(path),
        "revision": None,
        "access": "available",
        "selection_origin": "explicit",
        "source": "user:turn-1",
        "evidence": [{"path": "task.json", "digest": None}],
    }
    result = checker.identify(tmp_path, [record])
    assert result["status"] == "fail"
    assert result["diagnostics"][0]["code"] == "identity.reference_invalid"


def test_config_difference_preserves_json_types_and_unknowns():
    checker = load_checker()
    assert hasattr(checker, "compare_config"), "Configuration measurement is not implemented"
    result = checker.compare_config(
        {"agent": {"enabled": True}, "provider": "harbor", "environment": None},
        {"agent": {"enabled": 1}, "provider": "harbor", "environment": None},
    )
    assert result["differences"] == ["/agent/enabled"]
    assert "/environment" in result["unknowns"]
    assert result["acceptable"] is None
    assert result["status"] == "different"


def test_dispatch_selects_only_explicit_route_and_transitive_inputs(tmp_path):
    import json

    from eval_author_shared.task_evidence import digest

    checker = load_checker()
    assert hasattr(checker, "resolve_route"), "Declared route resolution is not implemented"
    files: dict[str, Any] = {
        "entry": {"selector": "strict", "routes": {"strict": "scorer", "unused": "other"}},
        "scorer": {},
        "prompt": {},
        "input": {},
        "other": {},
    }
    records: list[dict] = []
    for name, value in files.items():
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value))
        records.append(
            {
                "id": name,
                "role": "scorer" if name == "scorer" else "input",
                "path": path.name,
                "digest": digest(path),
                "revision": None,
                "access": "available",
                "selection_origin": "explicit",
                "source": "user:turn-1",
                "evidence": [{"path": path.name, "digest": digest(path)}],
            }
        )
    route = {
        "entrypoint": "entry",
        "expected_scorer": "scorer",
        "dependencies": {"scorer": ["prompt"], "prompt": ["input"], "input": []},
    }
    result = checker.resolve_route(tmp_path, records, route)
    assert result["status"] == "pass"
    assert result["selected"] == ["input", "prompt", "scorer"]
    files["entry"] = {"selector": None, "routes": files["entry"]["routes"]}
    entry = tmp_path / "entry.json"
    entry.write_text(json.dumps(files["entry"]))
    records[0]["digest"] = digest(entry)
    records[0]["evidence"][0]["digest"] = digest(entry)
    unresolved = checker.resolve_route(tmp_path, records, route)
    assert unresolved["status"] == "unknown"
    assert any(d["code"] == "route.dynamic_unresolved" for d in unresolved["diagnostics"])


@pytest.mark.parametrize("bad", [{}, [], True, 7])
def test_malformed_access_is_a_diagnostic_not_an_exception(tmp_path, bad):
    checker = load_checker()
    record = {
        "id": "task",
        "role": "task",
        "path": "task.json",
        "digest": None,
        "revision": None,
        "access": bad,
        "selection_origin": "explicit",
        "source": "turn-1",
        "evidence": [],
    }
    assert checker.identify(tmp_path, [record])["status"] == "fail"


def test_configuration_empty_sections_are_unknown():
    checker = load_checker()
    config = dict.fromkeys(checker.CONFIG_FIELDS, {})
    assert checker.compare_config(config, config)["status"] == "unknown"


@pytest.mark.parametrize("empty", ["", " \t\n"])
@pytest.mark.parametrize("side", ["declared", "observed", "both"])
def test_configuration_blank_sections_are_unknown(empty, side):
    checker = load_checker()
    declared = dict.fromkeys(checker.CONFIG_FIELDS, "known")
    observed = declared.copy()
    if side in ("declared", "both"):
        declared["provider"] = empty
    if side in ("observed", "both"):
        observed["provider"] = empty
    result = checker.compare_config(declared, observed)
    assert result["status"] == "unknown"
    assert result["unknowns"] == ["/provider"]
    assert result["differences"] == []


def test_configuration_known_empty_lists_remain_equal():
    checker = load_checker()
    config = dict.fromkeys(checker.CONFIG_FIELDS, [])
    result = checker.compare_config(config, config)
    assert result["status"] == "equal"
    assert result["unknowns"] == result["differences"] == []


def test_configuration_diagnostics_use_exact_json_pointers():
    checker = load_checker()
    declared: dict[str, Any] = dict.fromkeys(checker.CONFIG_FIELDS, "known")
    observed = declared.copy()
    declared["agent"] = {"a/b~c": ["", {"x/y": True}]}
    observed["agent"] = {"a/b~c": ["known", {"x/y": 1}]}
    declared["extra/~"] = {"": None}
    observed["extra/~"] = {"": "known"}
    result = checker.compare_config(declared, observed)
    assert result["status"] == "different"
    assert result["differences"] == ["/agent/a~1b~0c/1/x~1y"]
    assert result["unknowns"] == ["/agent/a~1b~0c/0", "/extra~1~0/"]


@pytest.fixture
def route_selection(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    from run_rubric_measurements import fixture

    return fixture(tmp_path, None)


@pytest.mark.parametrize("access", ["missing", "denied", "unresolved", "malformed"])
def test_route_ignores_untraversed_artifact_identity(tmp_path, route_selection, access):
    other = next(row for row in route_selection["artifacts"] if row["id"] == "other")
    other["access"] = access
    result = load_checker().resolve_route(tmp_path, route_selection["artifacts"], route_selection["route"])
    assert result["status"] == "pass"
    assert result["diagnostics"] == []
    assert result["selected"] == ["prompt", "scorer"]


def test_wrong_scorer_is_not_suppressed_by_unrelated_unknown(tmp_path, route_selection):
    route_selection["route"]["expected_scorer"] = "other"
    next(row for row in route_selection["artifacts"] if row["id"] == "task")["access"] = "missing"
    result = load_checker().resolve_route(tmp_path, route_selection["artifacts"], route_selection["route"])
    assert result["status"] == "fail"
    assert result["diagnostics"] == [{"code": "route.scorer_mismatch", "subject": "scorer", "status": "fail"}]


def test_route_preserves_failures_and_unknowns_in_traversed_closure(tmp_path, route_selection):
    route_selection["route"]["expected_scorer"] = "other"
    next(row for row in route_selection["artifacts"] if row["id"] == "prompt")["access"] = "missing"
    (tmp_path / "scorer.json").write_text("changed")
    result = load_checker().resolve_route(tmp_path, route_selection["artifacts"], route_selection["route"])
    assert result["status"] == "fail"
    assert {row["code"] for row in result["diagnostics"]} == {
        "route.scorer_mismatch",
        "identity.digest_mismatch",
        "identity.access_missing",
    }
    assert result["selected"] == ["prompt", "scorer"]


@pytest.mark.parametrize("key", ["entry", "scorer", "prompt"])
@pytest.mark.parametrize("malformed", [False, True])
def test_route_does_not_choose_ambiguous_duplicate_identity(tmp_path, route_selection, key, malformed):
    duplicate = dict(next(row for row in route_selection["artifacts"] if row["id"] == key))
    if malformed:
        duplicate["extra"] = "invalid"
    route_selection["artifacts"].append(duplicate)
    result = load_checker().resolve_route(tmp_path, route_selection["artifacts"], route_selection["route"])
    assert result["status"] == "fail"
    assert {"code": "identity.duplicate", "subject": key, "status": "fail"} in result["diagnostics"]
    if key == "entry":
        assert result["scorer"] is None
        assert result["selected"] == []


def test_route_keeps_dependency_unknowns_after_invalid_scorer(tmp_path, route_selection):
    next(row for row in route_selection["artifacts"] if row["id"] == "scorer")["extra"] = "invalid"
    next(row for row in route_selection["artifacts"] if row["id"] == "prompt")["access"] = "missing"
    result = load_checker().resolve_route(tmp_path, route_selection["artifacts"], route_selection["route"])
    assert result["status"] == "fail"
    assert result["diagnostics"] == [
        {"code": "identity.access_missing", "subject": "prompt", "status": "unknown"},
        {"code": "identity.record_invalid", "subject": "scorer", "status": "fail"},
    ]
    assert result["selected"] == ["prompt", "scorer"]


@pytest.mark.parametrize("access", ["missing", "denied", "unresolved"])
def test_route_does_not_read_unavailable_entry(tmp_path, route_selection, access):
    next(row for row in route_selection["artifacts"] if row["id"] == "entry")["access"] = access
    result = load_checker().resolve_route(tmp_path, route_selection["artifacts"], route_selection["route"])
    assert result["status"] == "unknown"
    assert result["scorer"] is None
    assert result["selected"] == []


def test_dispatch_rechecks_the_bytes_it_parses(tmp_path, monkeypatch):
    from eval_author_shared.task_evidence import digest

    checker = load_checker()
    path = tmp_path / "entry.json"
    path.write_text('{"selector":"strict","routes":{"strict":"entry"}}')
    record = {
        "id": "entry",
        "role": "scorer",
        "path": path.name,
        "digest": digest(path),
        "revision": None,
        "access": "available",
        "selection_origin": "explicit",
        "source": "turn-1",
        "evidence": [{"path": path.name, "digest": digest(path)}],
    }
    original = checker.identify

    def changed_after_identity(root, records):
        result = original(root, records)
        path.write_text('{"selector":"other","routes":{"other":"entry"}}')
        return result

    monkeypatch.setattr(checker, "identify", changed_after_identity)
    result = checker.resolve_route(
        tmp_path,
        [record],
        {
            "entrypoint": "entry",
            "expected_scorer": "entry",
            "dependencies": {"entry": []},
        },
    )
    assert result["status"] == "fail"
    assert result["diagnostics"][0]["code"] == "route.dispatch_changed"
