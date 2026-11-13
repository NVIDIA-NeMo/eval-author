# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/eval-author-rubric/scripts/rubric_scoring.py"
IDS = (
    "DISC-01-A",
    "DISC-01-B",
    "DISC-01-C",
    "DISC-02-A",
    "DISC-02-B",
    "DISC-03-A",
    "AUD-02",
    "ENV-01",
    "ENV-02-A",
    "ENV-03",
    "CREATE-07",
)


def assess(measurements, selection, root=None):
    sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
    assert SCRIPT.is_file(), "canonical rubric assessment is not implemented"
    spec = importlib.util.spec_from_file_location("rubric_scoring", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.assess(measurements, selection, root=root)


def test_all_canonical_items_default_to_unassessed_even_when_raw_passes():
    measurements = {name: {"status": "pass"} for name in ("identity", "route", "config", "links", "approvals")}
    rows = assess(measurements, {})
    assert tuple(rows) == IDS
    for key, row in rows.items():
        assert row["item_id"] == key
        assert row["status"] == "NOT_ASSESSED"
        assert row["score"] is None
        assert row["evidence_strength"] is None
        assert row["limitations"]
        assert row["evidence_refs"] == []


def artifact(ident, role="task", access="available"):
    ref = {"path": ident + ".json", "digest": "a" * 64}
    return {
        "id": ident,
        "role": role,
        "path": ref["path"],
        "digest": ref["digest"],
        "revision": "selected-revision",
        "access": access,
        "selection_origin": "explicit selection",
        "source": "scope.json",
        "evidence": [ref],
    }


def inventory_packet():
    records = [artifact("scope", "manifest"), artifact("task")]
    scope = {"required": ["task"], "source": "scope", "unresolved_material": []}
    selection = {"artifacts": records, "rubric_scope": {"task_artifacts": scope}}
    return {"identity": {"status": "pass", "records": records, "diagnostics": []}}, selection


def bind_inventory_source(tmp_path, selection):
    import hashlib

    scope = selection["rubric_scope"]["task_artifacts"]
    record = next(row for row in selection["artifacts"] if row["id"] == scope["source"])
    path = tmp_path / record["path"]
    path.write_text(json.dumps({"task_artifacts": {key: scope[key] for key in ("required", "unresolved_material")}}))
    record["digest"] = hashlib.sha256(path.read_bytes()).hexdigest()
    record["evidence"][0]["digest"] = record["digest"]


def test_selected_task_access_without_source_bytes_cannot_establish_completeness():
    measurements, selection = inventory_packet()
    row = assess(measurements, selection)["DISC-01-A"]
    assert (row["status"], row["score"], row["evidence_strength"]) == ("NOT_ASSESSED", None, "E2")
    assert row["evidence_refs"]
    assert row["limitations"]
    assert row["measurements"] == {"required": 1, "accessible": 1, "missing": 0, "denied": 0, "unresolved": 0}


def test_scope_assertions_without_measured_source_are_e0_not_numeric():
    measurements, selection = inventory_packet()
    measurements["identity"]["records"] = measurements["identity"]["records"][1:]
    row = assess(measurements, selection)["DISC-01-A"]
    assert row["score"] is None
    assert row["evidence_strength"] == "E0"


def test_confirmed_missing_required_task_is_zero_but_declared_missing_is_unknown(tmp_path):
    measurements, selection = inventory_packet()
    measurements["identity"]["diagnostics"] = [
        {"code": "identity.artifact_missing", "subject": "task", "status": "unknown"}
    ]
    bind_inventory_source(tmp_path, selection)
    row = assess(measurements, selection, root=tmp_path)["DISC-01-A"]
    assert (row["score"], row["evidence_strength"]) == (0, "E2")
    assert row["measurements"]["missing"] == 1
    measurements["identity"]["records"][1]["access"] = "missing"
    measurements["identity"]["diagnostics"][0]["code"] = "identity.access_missing"
    row = assess(measurements, selection, root=tmp_path)["DISC-01-A"]
    assert row["score"] is None
    assert row["measurements"]["unresolved"] == 1


def test_readable_subset_with_known_identity_gap_is_partial_not_full(tmp_path):
    measurements, selection = inventory_packet()
    measurements["identity"]["records"].append(artifact("input", "fixture"))
    selection["rubric_scope"]["task_artifacts"]["required"].append("input")
    measurements["identity"]["diagnostics"] = [
        {"code": "identity.digest_mismatch", "subject": "input", "status": "fail"}
    ]
    bind_inventory_source(tmp_path, selection)
    row = assess(measurements, selection, root=tmp_path)["DISC-01-A"]
    assert (row["score"], row["evidence_strength"]) == (1, "E2")
    assert row["measurements"]["unresolved"] == 1


def test_missing_evidence_reference_does_not_prove_required_artifact_missing():
    measurements, selection = inventory_packet()
    measurements["identity"]["records"][1]["evidence"].append({"path": "elsewhere.json", "digest": "b" * 64})
    measurements["identity"]["diagnostics"] = [
        {"code": "identity.artifact_missing", "subject": "task", "status": "unknown"}
    ]
    assert assess(measurements, selection)["DISC-01-A"]["score"] is None


def route_packet(tmp_path, monkeypatch, *, expected="scorer", dependencies=None):
    import hashlib

    monkeypatch.syspath_prepend(str(ROOT / "skills/eval-author/scripts"))
    from rubric_identity import identify, resolve_route

    dependencies = {"scorer": []} if dependencies is None else dependencies
    records = [artifact("dispatch", "configuration"), artifact("scorer", "scorer")]
    payloads = {
        "dispatch": {
            "selector": "fixed",
            "routes": {"fixed": "scorer"},
            "dependencies": dependencies,
            "expected_scorer": expected,
        },
        "scorer": {"fixture": True},
    }
    for record in records:
        path = tmp_path / record["path"]
        path.write_text(json.dumps(payloads[record["id"]]))
        record["digest"] = hashlib.sha256(path.read_bytes()).hexdigest()
        record["evidence"][0]["digest"] = record["digest"]
    selection = {
        "artifacts": records,
        "route": {"entrypoint": "dispatch", "expected_scorer": expected, "dependencies": dependencies},
    }
    measurements = {
        "identity": identify(tmp_path, records),
        "route": resolve_route(tmp_path, records, selection["route"]),
    }
    return measurements, selection


def test_explicit_resolved_route_meets_bounded_two_not_runtime_readiness(tmp_path, monkeypatch):
    measurements, selection = route_packet(tmp_path, monkeypatch)
    rows = assess(measurements, selection, root=tmp_path)
    assert (rows["DISC-01-B"]["score"], rows["DISC-01-B"]["evidence_strength"]) == (2, "E2")
    assert rows["DISC-03-A"]["score"] is None


def test_wrong_selected_scorer_is_zero_and_ambiguity_is_partial(tmp_path, monkeypatch):
    measurements, selection = route_packet(tmp_path, monkeypatch, expected="other")
    assert assess(measurements, selection, root=tmp_path)["DISC-01-B"]["score"] == 0
    measurements, selection = route_packet(tmp_path, monkeypatch, dependencies={})
    assert assess(measurements, selection, root=tmp_path)["DISC-01-B"]["score"] == 1
    measurements["route"]["scorer"] = None
    assert assess(measurements, selection, root=tmp_path)["DISC-01-B"]["score"] is None


def test_grading_dependencies_need_source_bound_completeness_review(tmp_path):
    selection = source_selection(tmp_path)
    scope = selection["rubric_scope"]["grading_dependencies"]
    scope.pop("review_source")
    assert json.loads(run_cli(tmp_path, selection).stdout)["items"]["DISC-01-C"]["score"] is None
    scope["review_source"] = "review"
    assert json.loads(run_cli(tmp_path, selection).stdout)["items"]["DISC-01-C"]["score"] == 2


def test_task_inventory_requires_task_roles_and_reference_origin_binding():
    measurements, selection = inventory_packet()
    measurements["identity"]["records"][1]["role"] = "scorer"
    assert assess(measurements, selection)["DISC-01-A"]["score"] is None
    measurements["identity"]["records"][1]["role"] = "task"
    measurements["identity"]["records"][1]["source"] = "unrelated.json"
    assert assess(measurements, selection)["DISC-01-A"]["score"] is None


def test_unsupported_claims_keep_unknown_with_specific_gap_even_for_pass_flags():
    measurements, selection = inventory_packet()
    measurements.update(
        {
            name: {"status": "pass", "verified": True, "authority_authenticated": True}
            for name in ("config", "links", "approvals", "runtime", "fairness", "tools", "external_state")
        }
    )
    measurements["identity"]["diagnostics"] = [
        {"code": "identity.digest_mismatch", "subject": "task", "status": "fail"}
    ]
    rows = assess(measurements, selection)
    for ident in ("DISC-02-A", "DISC-02-B", "DISC-03-A", "AUD-02", "ENV-01", "ENV-02-A", "ENV-03", "CREATE-07"):
        assert rows[ident]["status"] == "NOT_ASSESSED"
        assert rows[ident]["score"] is None
    assert any("silently" in limit for limit in rows["CREATE-07"]["limitations"])


def test_assessment_does_not_mutate_measurements_or_selection():
    import copy

    measurements, selection = inventory_packet()
    before = copy.deepcopy((measurements, selection))
    assess(measurements, selection)
    assert (measurements, selection) == before


@pytest.mark.parametrize("required", [[{}], [True], "task", [], ["task", "task"]])
def test_invalid_required_declaration_cannot_score(required):
    measurements, selection = inventory_packet()
    selection["rubric_scope"]["task_artifacts"]["required"] = required
    assert assess(measurements, selection)["DISC-01-A"]["score"] is None


def test_measured_artifact_identity_must_match_the_current_selection():
    import copy

    measurements, selection = inventory_packet()
    selection = copy.deepcopy(selection)
    selection["artifacts"][1]["digest"] = "b" * 64
    assert assess(measurements, selection)["DISC-01-A"]["score"] is None


@pytest.mark.parametrize(
    "variant,task_score,route_score",
    [
        ("valid", 2, 2),
        ("wrong-scorer", 2, 0),
        ("dynamic", 2, None),
        ("missing", 0, 2),
        ("declared-missing", None, 2),
    ],
)
def test_demo_real_local_helpers(tmp_path, monkeypatch, variant, task_score, route_score):
    import hashlib

    monkeypatch.syspath_prepend(str(ROOT / "skills/eval-author/scripts"))
    from rubric_identity import identify, resolve_route

    payloads = {
        "scope": {"task_artifacts": {"required": ["task"], "unresolved_material": []}},
        "task": {"instruction": "Return a fixed demo answer."},
        "dispatch": {
            "selector": "selected",
            "routes": {"selected": "scorer"},
            "dependencies": {"scorer": []},
            "expected_scorer": "other" if variant == "wrong-scorer" else "scorer",
        },
        "scorer": {"scoring": "demo-only"},
    }
    if variant == "dynamic":
        payloads["dispatch"] = {"dynamic": "unresolved-code"}
    records = []
    for ident, payload in payloads.items():
        path = tmp_path / (ident + ".json")
        path.write_text(json.dumps(payload))
        record = artifact(
            ident, {"scope": "manifest", "task": "task", "dispatch": "configuration", "scorer": "scorer"}[ident]
        )
        record["digest"] = hashlib.sha256(path.read_bytes()).hexdigest()
        record["evidence"] = [{"path": path.name, "digest": record["digest"]}]
        records.append(record)
    selection = {
        "artifacts": records,
        "route": {
            "entrypoint": "dispatch",
            "expected_scorer": "other" if variant == "wrong-scorer" else "scorer",
            "dependencies": {"scorer": []},
        },
        "rubric_scope": {"task_artifacts": {"required": ["task"], "source": "scope", "unresolved_material": []}},
    }
    if variant in ("missing", "declared-missing"):
        (tmp_path / "task.json").unlink()
    if variant == "declared-missing":
        next(row for row in records if row["id"] == "task")["access"] = "missing"
    measurements = {
        "identity": identify(tmp_path, records),
        "route": resolve_route(tmp_path, records, selection["route"]),
    }
    rows = assess(measurements, selection, root=tmp_path)
    assert rows["DISC-01-A"]["score"] == task_score
    assert rows["DISC-01-B"]["score"] == route_score
    assert len(rows) == 11
    assert all(row["evidence_strength"] == "E2" for row in rows.values() if row["score"] is not None)


@pytest.mark.parametrize("declared", [True, False])
def test_denied_declaration_is_not_a_measured_read_failure(declared, tmp_path):
    measurements, selection = inventory_packet()
    if declared:
        measurements["identity"]["records"][1]["access"] = "denied"
    measurements["identity"]["diagnostics"] = [
        {"code": "identity.access_denied", "subject": "task", "status": "unknown"}
    ]
    bind_inventory_source(tmp_path, selection)
    assert assess(measurements, selection, root=tmp_path)["DISC-01-A"]["score"] == (None if declared else 0)


def test_defect_precedes_readable_partial_inventory(tmp_path):
    measurements, selection = inventory_packet()
    measurements["identity"]["records"].append(artifact("input", "fixture"))
    selection["rubric_scope"]["task_artifacts"]["required"].append("input")
    measurements["identity"]["diagnostics"] = [
        {"code": "identity.artifact_missing", "subject": "input", "status": "unknown"}
    ]
    bind_inventory_source(tmp_path, selection)
    assert assess(measurements, selection, root=tmp_path)["DISC-01-A"]["score"] == 0


def test_review_source_must_be_a_review_of_the_selected_inventory():
    measurements, selection = inventory_packet()
    records = measurements["identity"]["records"]
    records[1]["role"] = "test"
    scope = selection["rubric_scope"].pop("task_artifacts")
    selection["rubric_scope"]["grading_dependencies"] = scope
    scope["review_source"] = "scope"
    assert assess(measurements, selection)["DISC-01-C"]["score"] is None


def test_source_only_declared_configuration_is_assertion_not_numeric():
    rows = assess({"config": {"status": "equal"}}, {"declared_config": {"agent": "demo"}})
    assert rows["DISC-02-A"]["evidence_strength"] == "E0"
    assert rows["DISC-02-A"]["score"] is None


def test_invalid_materiality_review_cannot_be_interpreted_as_complete():
    measurements, selection = inventory_packet()
    selection["rubric_scope"]["task_artifacts"]["unresolved_material"] = "none"
    assert assess(measurements, selection)["DISC-01-A"]["score"] is None


def source_selection(tmp_path, inventory=None, review=None):
    import hashlib

    inventory = inventory if inventory is not None else {"required": ["task"], "unresolved_material": []}
    payloads = {
        "scope": {"task_artifacts": inventory, "grading_dependencies": inventory},
        "task": {"instruction": "fixed answer"},
        "review": review
        if review is not None
        else {
            "source": "scope",
            "required": ["task"],
            "unresolved_material": [],
            "complete": True,
        },
    }
    records = []
    for ident, payload in payloads.items():
        path = tmp_path / (ident + ".json")
        path.write_text(json.dumps(payload))
        record = artifact(ident, {"scope": "manifest", "task": "test", "review": "review"}[ident])
        record["digest"] = hashlib.sha256(path.read_bytes()).hexdigest()
        record["evidence"] = [{"path": path.name, "digest": record["digest"]}]
        records.append(record)
    return {
        "artifacts": records,
        "rubric_scope": {
            "grading_dependencies": {
                "required": ["task"],
                "source": "scope",
                "unresolved_material": [],
                "review_source": "review",
            }
        },
    }


@pytest.mark.parametrize(
    "review",
    [
        {"source": "scope", "required": ["task"], "unresolved_material": [], "complete": False},
        {"source": "scope", "required": ["task"], "unresolved_material": ["missing test"], "complete": True},
        {"complete": False, "gaps": ["missing test"]},
        {"opaque": "reviewed"},
    ],
)
def test_cli_review_contents_must_substantiate_completeness(tmp_path, review):
    result = run_cli(tmp_path, source_selection(tmp_path, review=review))
    assert result.returncode == 1
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["measurements"]["identity"]["status"] == "pass"
    row = report["items"]["DISC-01-C"]
    assert (row["status"], row["score"]) == ("NOT_ASSESSED", None)


@pytest.mark.parametrize("kind,item", [("task_artifacts", "DISC-01-A"), ("grading_dependencies", "DISC-01-C")])
@pytest.mark.parametrize(
    "inventory",
    [
        {"required": ["task", "omitted"], "unresolved_material": []},
        {"required": ["task"], "unresolved_material": ["unresolved reference"]},
        {"task_artifacts": ["task"]},
    ],
)
def test_cli_inventory_projection_cannot_hide_source_requirements(tmp_path, kind, item, inventory):
    selection = source_selection(tmp_path, inventory=inventory)
    if kind == "task_artifacts":
        selection["rubric_scope"][kind] = selection["rubric_scope"].pop("grading_dependencies")
        selection["artifacts"][1]["role"] = "task"
    result = run_cli(tmp_path, selection)
    assert result.returncode == 1
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["measurements"]["identity"]["status"] == "pass"
    assert (report["items"][item]["status"], report["items"][item]["score"]) == ("NOT_ASSESSED", None)


@pytest.mark.parametrize("kind,item", [("task_artifacts", "DISC-01-A"), ("grading_dependencies", "DISC-01-C")])
def test_cli_supported_source_projection_permits_full_credit(tmp_path, kind, item):
    selection = source_selection(tmp_path)
    if kind == "task_artifacts":
        selection["rubric_scope"][kind] = selection["rubric_scope"].pop("grading_dependencies")
        selection["artifacts"][1]["role"] = "task"
    result = run_cli(tmp_path, selection)
    assert result.returncode == 1  # Other raw helpers remain unknown.
    assert result.stderr == ""
    row = json.loads(result.stdout)["items"][item]
    assert (row["status"], row["score"], row["evidence_strength"]) == ("ASSESSED", 2, "E2")


@pytest.mark.parametrize("name", ["scope", "review"])
@pytest.mark.parametrize("remeasure", [False, True])
@pytest.mark.parametrize("variant", range(6))
def test_changed_or_unsupported_source_bytes_cannot_award_full_credit(tmp_path, monkeypatch, name, variant, remeasure):
    monkeypatch.syspath_prepend(str(ROOT / "skills/eval-author/scripts"))
    from rubric_identity import identify

    selection = source_selection(tmp_path)
    measurements = {"identity": identify(tmp_path, selection["artifacts"])}
    assert measurements["identity"]["status"] == "pass"
    replacement = [
        b'{"complete": false}',
        b'{"complete": true, "complete": false}',
        b"not JSON",
        b"null",
        b"\xff",
        b" " * (1024 * 1024 + 1),
    ][variant]
    (tmp_path / (name + ".json")).write_bytes(replacement)
    if remeasure:
        import hashlib

        record = next(row for row in selection["artifacts"] if row["id"] == name)
        record["digest"] = hashlib.sha256(replacement).hexdigest()
        record["evidence"][0]["digest"] = record["digest"]
        measurements = {"identity": identify(tmp_path, selection["artifacts"])}
        assert measurements["identity"]["status"] == ("unknown" if variant == 5 else "pass")
    assert assess(measurements, selection, root=tmp_path)["DISC-01-C"]["score"] is None


def test_measured_materiality_gap_retains_partial_credit(tmp_path):
    inventory = {"required": ["task"], "unresolved_material": ["unresolved reference"]}
    selection = source_selection(tmp_path, inventory=inventory)
    selection["rubric_scope"]["grading_dependencies"]["unresolved_material"] = inventory["unresolved_material"]
    row = json.loads(run_cli(tmp_path, selection).stdout)["items"]["DISC-01-C"]
    assert (row["score"], row["evidence_strength"]) == (1, "E2")


def test_unmeasured_materiality_assertion_cannot_establish_partial_content_finding():
    measurements, selection = inventory_packet()
    selection["rubric_scope"]["task_artifacts"]["unresolved_material"] = ["asserted gap"]
    assert assess(measurements, selection)["DISC-01-A"]["score"] is None


@pytest.mark.parametrize("kind,item", [("task_artifacts", "DISC-01-A"), ("grading_dependencies", "DISC-01-C")])
@pytest.mark.parametrize("state", ["missing", "partial"])
def test_unbacked_inventory_cannot_assign_negative_or_partial_merit(tmp_path, kind, item, state):
    selection = source_selection(tmp_path)
    scope = selection["rubric_scope"].pop("grading_dependencies")
    selection["rubric_scope"][kind] = scope
    if kind == "task_artifacts":
        selection["artifacts"][1]["role"] = "task"
    if state == "missing":
        scope["required"] = ["typo"]
        selection["artifacts"].append(artifact("typo", "task" if kind == "task_artifacts" else "test"))
    else:
        scope["required"].append("unrecorded")
    result = run_cli(tmp_path, selection)
    assert result.stderr == ""
    row = json.loads(result.stdout)["items"][item]
    assert (row["status"], row["score"]) == ("NOT_ASSESSED", None)


@pytest.mark.parametrize("name", ["scope", "review"])
def test_deep_source_json_retains_unknown_without_traceback(tmp_path, monkeypatch, name):
    import hashlib

    monkeypatch.syspath_prepend(str(ROOT / "skills/eval-author/scripts"))
    from rubric_identity import identify

    selection = source_selection(tmp_path)
    data = b'{"nested":' + b"[" * 10000 + b"0" + b"]" * 10000 + b"}"
    (tmp_path / (name + ".json")).write_bytes(data)
    record = next(row for row in selection["artifacts"] if row["id"] == name)
    record["digest"] = hashlib.sha256(data).hexdigest()
    record["evidence"][0]["digest"] = record["digest"]
    measurements = {"identity": identify(tmp_path, selection["artifacts"])}
    assert measurements["identity"]["status"] == "pass"
    assert assess(measurements, selection, root=tmp_path)["DISC-01-C"]["score"] is None
    result = run_cli(tmp_path, selection)
    assert result.stderr == ""
    assert json.loads(result.stdout)["items"]["DISC-01-C"]["score"] is None


@pytest.mark.parametrize("replacement", ["symlink", "fifo"])
def test_source_read_rejects_replacement_after_path_validation(tmp_path, monkeypatch, replacement):
    import os

    monkeypatch.syspath_prepend(str(ROOT / "skills/eval-author/scripts"))
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    import rubric_identity
    import rubric_scoring

    selection = source_selection(tmp_path)
    record = selection["artifacts"][0]
    path = tmp_path / record["path"]
    target = tmp_path / "replacement.json"
    target.write_bytes(path.read_bytes())
    original = rubric_identity.local_file

    def replace_after_validation(root, relative):
        selected = original(root, relative)
        if selected == path:
            path.unlink()
            if replacement == "symlink":
                path.symlink_to(target)
            else:
                os.mkfifo(path)
        return selected

    monkeypatch.setattr(rubric_identity, "local_file", replace_after_validation)
    if hasattr(rubric_scoring, "local_file"):
        monkeypatch.setattr(rubric_scoring, "local_file", replace_after_validation)
    assert rubric_scoring._source_json(tmp_path, record) is None


@pytest.mark.parametrize("replacement", ["symlink", "fifo"])
def test_source_read_never_opens_replacement_after_regular_file_probe(tmp_path, monkeypatch, replacement):
    import os
    import signal

    monkeypatch.syspath_prepend(str(ROOT / "skills/eval-author/scripts"))
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    import rubric_scoring

    selection = source_selection(tmp_path)
    record = selection["artifacts"][0]
    path = tmp_path / record["path"]
    target = tmp_path / "replacement.json"
    target.write_bytes(path.read_bytes())
    original = Path.is_file

    def replace_after_probe(selected):
        regular = original(selected)
        if selected == path:
            path.unlink()
            if replacement == "symlink":
                path.symlink_to(target)
            else:
                os.mkfifo(path)
        return regular

    def timeout(signum, frame):
        raise AssertionError("source read blocked on a replacement FIFO")

    monkeypatch.setattr(Path, "is_file", replace_after_probe)
    previous = signal.signal(signal.SIGALRM, timeout)
    try:
        signal.alarm(2)
        result = rubric_scoring._source_json(tmp_path, record)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    assert result == json.loads(target.read_text())
    assert not path.is_symlink()


def run_cli(tmp_path, selection):
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(selection))
    return subprocess.run(
        [sys.executable, str(SCRIPT.with_name("measure.py")), "--root", str(tmp_path), "--selection", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("scope", [None, [], True, "private-invalid-scope"])
def test_cli_rejects_nonobject_rubric_scope_without_traceback(tmp_path, scope):
    result = run_cli(tmp_path, {"rubric_scope": scope})
    assert result.returncode == 2
    assert result.stderr == ""
    assert json.loads(result.stdout)["code"] == "selection.invalid"
    with pytest.raises(ValueError):
        assess({}, {"rubric_scope": scope})


@pytest.mark.parametrize("source", [[], {}, 1, None])
def test_malformed_source_id_does_not_crash_or_award_score(source):
    measurements, selection = inventory_packet()
    selection["rubric_scope"]["task_artifacts"]["source"] = source
    assert assess(measurements, selection)["DISC-01-A"]["score"] is None
