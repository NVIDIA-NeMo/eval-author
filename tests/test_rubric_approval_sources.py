# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"
sys.path.insert(0, str(SCRIPTS))
import rubric_identity as identity  # noqa: E402
import rubric_links as links  # noqa: E402


def retain(root, ident, role, value):
    path = root / f"{ident}.json"
    path.write_text(json.dumps(value))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "id": ident,
        "role": role,
        "path": path.name,
        "digest": digest,
        "revision": None,
        "access": "available",
        "selection_origin": "explicit test selection",
        "source": "synthetic local evidence",
        "evidence": [{"path": path.name, "digest": digest}],
    }


def fixture(root):
    records = [
        retain(root, ident, role, {"name": ident})
        for ident, role in (
            ("ethos", "ethos"),
            ("task", "task"),
            ("scorer", "scorer"),
            ("old", "dependency"),
            ("new", "dependency"),
        )
    ]
    approval = {
        "id": "a1",
        "decision_id": "d1",
        "scope": "task:task/substitution",
        "substitution": {"from": "old", "to": "new"},
        "bindings": {row["id"]: row["digest"] for row in records},
    }
    decision = {key: copy.deepcopy(value) for key, value in approval.items() if key not in {"id", "decision_id"}}
    decision.update(id="d1", outcome="approved", source="retained conversation:turn-17")
    records.append(retain(root, "decision", "decision", {"decisions": [decision]}))
    return (
        [approval],
        [decision],
        identity.identify(root, records),
        {"d1": {"artifact_id": "decision", "pointer": "/decisions/0"}},
    )


def test_retained_decision_binds_exact_content_without_authenticating_authority(tmp_path):
    data = fixture(tmp_path)
    before = copy.deepcopy(data)
    assert hasattr(links, "check_sourced_approvals"), "Caller projections must be checked against retained decisions"
    result = links.check_sourced_approvals(tmp_path, *data)
    assert result["status"] == "pass", result
    assert result["diagnostics"] == []
    assert result["authority_authenticated"] is False
    assert result["evidence"] == [
        {
            "decision_id": "d1",
            "artifact_id": "decision",
            "pointer": "/decisions/0",
            "digest": data[2]["records"][-1]["digest"],
        }
    ]
    assert data == before


@pytest.mark.parametrize("contradiction", [False, True])
def test_cli_uses_retained_decision_source(tmp_path, contradiction):
    approvals, decisions, selected, sources = fixture(tmp_path)
    if contradiction:
        retained = {**decisions[0], "outcome": "denied"}
        selected["records"][-1] = retain(tmp_path, "decision", "decision", {"decisions": [retained]})
    request = tmp_path / "selection.json"
    request.write_text(
        json.dumps(
            {
                "artifacts": selected["records"],
                "approvals": approvals,
                "decisions": decisions,
                "approval_sources": sources,
            }
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS.parents[1] / "eval-author-rubric/scripts/measure.py"),
            "--root",
            str(tmp_path),
            "--selection",
            str(request),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert result.stderr == ""
    report = json.loads(result.stdout)
    measured = report["measurements"]["approvals"]
    assert measured["status"] == ("fail" if contradiction else "pass")
    if contradiction:
        assert "approvals.source_mismatch" in {row["code"] for row in measured["diagnostics"]}
    else:
        assert measured["evidence"][0]["decision_id"] == "d1"
    assert report["preflight_success"] is False


def test_supporting_source_cannot_narrow_away_material_dependency(tmp_path):
    approvals, decisions, selected, sources = fixture(tmp_path)
    selected["records"][-1]["role"] = "dependency"
    result = links.check_sourced_approvals(tmp_path, approvals, decisions, selected, sources)
    assert result["status"] == "fail"
    assert "approvals.source_role_invalid" in {row["code"] for row in result["diagnostics"]}


def test_retained_contradictory_outcome_is_not_ignored(tmp_path):
    approvals, decisions, selected, sources = fixture(tmp_path)
    actual = {**decisions[0], "status": "denied"}
    selected["records"][-1] = retain(tmp_path, "decision", "decision", {"decisions": [actual]})
    result = links.check_sourced_approvals(tmp_path, approvals, decisions, selected, sources)
    assert result["status"] == "fail"
    assert "approvals.source_record_invalid" in {row["code"] for row in result["diagnostics"]}


def test_upstream_identity_failure_cannot_be_laundered(tmp_path):
    approvals, decisions, selected, sources = fixture(tmp_path)
    selected["status"] = "fail"
    selected["diagnostics"] = [{"code": "identity.record_invalid", "subject": "omitted", "status": "fail"}]
    result = links.check_sourced_approvals(tmp_path, approvals, decisions, selected, sources)
    assert result["status"] == "fail"
    assert selected["diagnostics"][0] in result["diagnostics"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("outcome", "denied"),
        ("source", "a different retained turn"),
        ("scope", "task:elsewhere/substitution"),
        ("substitution", {"from": "new", "to": "old"}),
        ("bindings", {"task": "a" * 64}),
        ("id", "different-decision"),
    ],
)
def test_projected_fields_cannot_contradict_retained_decision(tmp_path, field, value):
    approvals, decisions, selected, sources = fixture(tmp_path)
    retained = {**decisions[0], field: value}
    selected["records"][-1] = retain(tmp_path, "decision", "decision", {"decisions": [retained]})
    result = links.check_sourced_approvals(tmp_path, approvals, decisions, selected, sources)
    assert result["status"] == "fail"
    assert "approvals.source_mismatch" in {row["code"] for row in result["diagnostics"]}
    assert result["evidence"] == []
    assert "retained turn" not in json.dumps(result)


@pytest.mark.parametrize("pointer", ["decisions/0", "/decisions/~2", "/decisions/\n", 0, None])
def test_invalid_pointer_syntax_fails_without_reading(tmp_path, pointer, monkeypatch):
    approvals, decisions, selected, sources = fixture(tmp_path)
    sources["d1"]["pointer"] = pointer
    monkeypatch.setattr(identity, "_read_selected", lambda *_: pytest.fail("Invalid pointer must not read"))
    result = links.check_sourced_approvals(tmp_path, approvals, decisions, selected, sources)
    assert result["status"] == "fail"
    assert "approvals.source_invalid" in {row["code"] for row in result["diagnostics"]}


@pytest.mark.parametrize(
    "pointer", ["/decisions/-1", "/decisions/00", "/decisions/1", "/missing", "/decisions/99999999999"]
)
def test_pointer_never_aliases_missing_or_noncanonical_array_index(tmp_path, pointer):
    approvals, decisions, selected, sources = fixture(tmp_path)
    sources["d1"]["pointer"] = pointer
    result = links.check_sourced_approvals(tmp_path, approvals, decisions, selected, sources)
    assert result["status"] == "fail"
    assert "approvals.source_pointer_missing" in {row["code"] for row in result["diagnostics"]}


def test_escaped_pointer_selects_exact_retained_key(tmp_path):
    approvals, decisions, selected, sources = fixture(tmp_path)
    selected["records"][-1] = retain(tmp_path, "decision", "decision", {"a/b~c": decisions[0]})
    sources["d1"]["pointer"] = "/a~1b~0c"
    assert links.check_sourced_approvals(tmp_path, approvals, decisions, selected, sources)["status"] == "pass"


@pytest.mark.parametrize(
    "mutation", ["missing", "denied", "unresolved", "stale", "unselected", "no_mapping", "symlink"]
)
def test_unavailable_or_unselected_decision_cannot_pass(tmp_path, mutation):
    approvals, decisions, selected, sources = fixture(tmp_path)
    artifact = selected["records"][-1]
    path = tmp_path / artifact["path"]
    if mutation in {"denied", "unresolved"}:
        artifact["access"] = mutation
    elif mutation == "missing":
        path.unlink()
    elif mutation == "stale":
        path.write_text("{}")
    elif mutation == "unselected":
        selected["records"].pop()
    elif mutation == "no_mapping":
        sources.clear()
    elif mutation == "symlink":
        path.rename(tmp_path / "actual.json")
        path.symlink_to(tmp_path / "actual.json")
    result = links.check_sourced_approvals(tmp_path, approvals, decisions, selected, sources)
    assert result["status"] != "pass"
    assert result["evidence"] == []
    assert result["authority_authenticated"] is False


@pytest.mark.parametrize("which", ["audit", "journey"])
def test_existing_supported_native_checkpoints_cannot_supply_missing_substitution_fields(tmp_path, which):
    import audit_state
    import journey_state

    approvals, decisions, selected, sources = fixture(tmp_path)
    if which == "audit":
        document = audit_state.new_record()
        document["checks_approval"] = {"digest": "a" * 64, "source": decisions[0]["source"]}
        audit_state.validate(document)
        pointer = "/checks_approval"
    else:
        document = {
            "tasks": {
                "task": {
                    "status": "accepted",
                    "source": decisions[0]["source"],
                    "task_digest": "a" * 64,
                    "evidence": [{"path": "proof.json", "digest": "b" * 64}],
                    "updated_at": "retained timestamp",
                }
            }
        }
        journey_state._validate_tasks(document["tasks"])
        pointer = "/tasks/task"
    selected["records"][-1] = retain(tmp_path, "decision", "decision", document)
    sources["d1"]["pointer"] = pointer
    result = links.check_sourced_approvals(tmp_path, approvals, decisions, selected, sources)
    assert result["status"] == "unknown"
    assert result["diagnostics"] == [
        {
            "code": "approvals.source_record_incomplete",
            "subject": "d1",
            "status": "unknown",
        }
    ]


@pytest.mark.parametrize("mutation", ["dependency", "self_reference", "source_endpoint", "stale_material"])
def test_scope_is_complete_and_nonrecursive(tmp_path, mutation):
    approvals, decisions, selected, sources = fixture(tmp_path)
    if mutation == "dependency":
        selected["records"].append(retain(tmp_path, "extra", "dependency", {"extra": True}))
    elif mutation == "stale_material":
        (tmp_path / "scorer.json").write_text("{}")
    else:
        for row in [*approvals, *decisions]:
            if mutation == "self_reference":
                row["bindings"]["decision"] = selected["records"][-1]["digest"]
            else:
                row["substitution"]["to"] = "decision"
    result = links.check_sourced_approvals(tmp_path, approvals, decisions, selected, sources)
    assert result["status"] == "fail"


@pytest.mark.parametrize("payload", [b'{"decisions": [], "decisions": []}', b'{"x": NaN}', b"\xff", b"[" * 1000])
def test_invalid_retained_json_never_passes(tmp_path, payload):
    approvals, decisions, selected, sources = fixture(tmp_path)
    artifact = selected["records"][-1]
    (tmp_path / artifact["path"]).write_bytes(payload)
    artifact["digest"] = hashlib.sha256(payload).hexdigest()
    artifact["evidence"][0]["digest"] = artifact["digest"]
    result = links.check_sourced_approvals(tmp_path, approvals, decisions, selected, sources)
    assert result["status"] == "fail"
    assert "approvals.source_invalid" in {row["code"] for row in result["diagnostics"]}


def test_selected_files_are_read_once_and_not_discovered(tmp_path, monkeypatch):
    data = fixture(tmp_path)
    (tmp_path / "unselected.json").write_text("must never be opened")
    read = identity._read_selected
    calls = []

    def spy(root, path, limit):
        calls.append(path)
        return read(root, path, limit)

    monkeypatch.setattr(identity, "_read_selected", spy)
    assert links.check_sourced_approvals(tmp_path, *data)["status"] == "pass"
    assert sorted(calls) == sorted(row["path"] for row in data[2]["records"])


@pytest.mark.parametrize("limit", ["MAX_SOURCE_BYTES", "MAX_TOTAL_BYTES", "MAX_FILES", "MAX_RECORDS"])
def test_shared_read_and_selection_limits_fail_closed(tmp_path, monkeypatch, limit):
    data = fixture(tmp_path)
    monkeypatch.setattr(identity, limit, 1)
    assert links.check_sourced_approvals(tmp_path, *data)["status"] != "pass"
