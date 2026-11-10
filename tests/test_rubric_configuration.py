# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"
sys.path.insert(0, str(SCRIPTS))


def checker():
    path = SCRIPTS / "rubric_configuration.py"
    spec = importlib.util.spec_from_file_location("rubric_configuration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def artifact(root, name, content, *, role="configuration", suffix=".json"):
    data = content.encode() if isinstance(content, str) else json.dumps(content).encode()
    path = root / (name + suffix)
    path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    return {
        "id": name,
        "role": role,
        "path": path.name,
        "digest": sha,
        "revision": None,
        "access": "available",
        "selection_origin": "explicit",
        "source": "user:selection",
        "evidence": [{"path": path.name, "digest": sha}],
    }


def ref(artifact_id, pointer):
    return {"artifact_id": artifact_id, "pointer": pointer}


@pytest.mark.parametrize(
    "observed,status",
    [
        ({"model": "b", "stop": None}, "different"),
        ({"model": "a", "stop": None}, "unknown"),
        ({"stop": None}, "unknown"),
    ],
)
def test_known_nested_difference_survives_unknown_neighbors(tmp_path, observed, status):
    records = [artifact(tmp_path, "declared", {"model": "a", "stop": None}), artifact(tmp_path, "observed", observed)]
    result = checker().compare_sourced_config(
        tmp_path, records, {"/agent": ref("declared", "")}, {"/agent": ref("observed", "")}
    )
    assert result["fields"]["/agent"]["status"] == status
    assert result["differences"] == (["/agent"] if status == "different" else [])
    assert "model" not in json.dumps(result)
    assert "stop" not in json.dumps(result)


def test_extracts_selected_fields_without_disclosing_values(tmp_path):
    module = checker()
    values = {
        "agent": "private-agent",
        "run": "private-run",
        "task": "private-task",
        "tool_permissions": ["private-tool"],
        "provider": "credential-sentinel",
        "runtime_pins": {"python": "private-version"},
        "environment": {"TOKEN": "private-token"},
    }
    records = [artifact(tmp_path, name, values) for name in ("declared", "observed")]
    declared = {"/" + key: ref("declared", "/" + key) for key in values}
    observed = {"/" + key: ref("observed", "/" + key) for key in values}
    result = module.compare_sourced_config(tmp_path, records, declared, observed)
    assert result["status"] == "equal"
    assert result["differences"] == []
    assert result["unknowns"] == []
    assert result["counts"] == {
        "fields": 7,
        "compared": 7,
        "equal": 7,
        "different": 0,
        "unknown": 0,
        "declared_sourced": 7,
        "observed_sourced": 7,
        "run_bound": 0,
    }
    assert result["acceptable"] is None
    assert result["score"] is None
    assert result["native_attestation"] is False
    assert len(result["fields"]) == 7
    provider = result["fields"]["/provider"]
    assert provider["declared"]["state"] == "sourced"
    assert provider["observed"]["provenance"] == {
        "artifact_id": "observed",
        "pointer": "/provider",
        "digest": records[1]["digest"],
        "format": "json",
    }
    assert provider["observed"]["binding"]["status"] == "unbound"
    encoded = json.dumps(result)
    assert "credential-sentinel" not in encoded
    assert "private-" not in encoded
    assert "TOKEN" not in encoded
    assert result == module.compare_sourced_config(tmp_path, records[::-1], declared, observed)


@pytest.mark.parametrize(
    "content,suffix",
    [
        ('{"tool_permissions": []}', ".json"),
        ("tool_permissions = []\n", ".toml"),
        ('{"tool_permissions": {"allow": []}}', ".json"),
        ("[tool_permissions]\nallow = []\n", ".toml"),
        ('{"tool_permissions": [{"allow": []}]}', ".json"),
        ("[[tool_permissions]]\nallow = []\n", ".toml"),
    ],
)
def test_explicit_empty_permission_lists_are_sourced(tmp_path, content, suffix):
    records = [artifact(tmp_path, name, content, suffix=suffix) for name in ("declared", "observed")]
    result = checker().compare_sourced_config(
        tmp_path,
        records,
        {"/tool_permissions": ref("declared", "/tool_permissions")},
        {"/tool_permissions": ref("observed", "/tool_permissions")},
    )
    field = result["fields"]["/tool_permissions"]
    assert field["status"] == "equal"
    for side in ("declared", "observed"):
        assert field[side]["state"] == "sourced"
        assert field[side]["reason"] is None
        assert field[side]["provenance"]["format"] == suffix[1:]
    assert result["counts"]["compared"] == 1
    assert result["counts"]["unknown"] == 6
    assert "/tool_permissions" not in result["unknowns"]
    assert result["acceptable"] is None
    assert result["native_attestation"] is False


@pytest.mark.parametrize(
    "observed,status,reason",
    [
        ("tool_permissions = []\n", "equal", None),
        ('tool_permissions = ["read"]\n', "different", None),
        ("tool_permissions = [[]]\n", "different", None),
        ('tool_permissions = [[], "read"]\n', "different", None),
        ("[tool_permissions]\n", "unknown", "empty_value"),
        ('tool_permissions = "  "\n', "unknown", "empty_value"),
        ('tool_permissions = [[], ""]\n', "different", "empty_value"),
        ('tool_permissions = [{allow = [], unknown = ""}]\n', "different", "empty_value"),
        ("unrelated = []\n", "unknown", "pointer_missing"),
    ],
)
def test_empty_json_permissions_compare_with_toml_without_dropping_values(tmp_path, observed, status, reason):
    records = [
        artifact(tmp_path, "declared", {"tool_permissions": []}),
        artifact(tmp_path, "observed", observed, suffix=".toml"),
    ]
    result = checker().compare_sourced_config(
        tmp_path,
        records,
        {"/tool_permissions": ref("declared", "/tool_permissions")},
        {"/tool_permissions": ref("observed", "/tool_permissions")},
    )
    field = result["fields"]["/tool_permissions"]
    assert field["declared"]["state"] == "sourced"
    assert field["status"] == status
    assert field["observed"]["reason"] == reason
    assert result["counts"]["compared"] == (status != "unknown")
    assert ("/tool_permissions" in result["unknowns"]) == (status == "unknown")
    assert ("/tool_permissions" in result["differences"]) == (status == "different")


def test_unknown_states_and_claim_limits_remain_distinct(tmp_path):
    module = checker()
    record = artifact(tmp_path, "declared", {"agent": None, "run": "", "task": {}, "environment": [None]})
    declared = {
        "/agent": ref("declared", "/agent"),
        "/run": ref("declared", "/run"),
        "/task": ref("declared", "/task"),
        "/environment": ref("declared", "/environment"),
        "/provider": {"state": "provider_hidden", "reason": "provider_not_disclosed"},
        "/runtime_pins": {"state": "inaccessible", "reason": "access_denied"},
    }
    observed = {"/provider": {"state": "unobservable", "reason": "not_recorded"}}
    result = module.compare_sourced_config(tmp_path, [record], declared, observed)
    assert result["status"] == "unknown"
    assert result["counts"]["unknown"] == 7
    assert result["counts"]["compared"] == 0
    assert result["fields"]["/provider"]["declared"]["state"] == "provider_hidden"
    assert result["fields"]["/provider"]["observed"]["state"] == "unobservable"
    assert result["fields"]["/runtime_pins"]["declared"]["state"] == "inaccessible"
    assert result["fields"]["/tool_permissions"]["declared"]["state"] == "omitted"
    for path, field in result["fields"].items():
        for side in ("declared", "observed"):
            assert field[side]["reason"]
            assert field[side]["claim_limit"]
        assert result["unknown_reasons"][path]
    for name in ("agent", "run", "task", "environment"):
        assert result["fields"]["/" + name]["declared"]["state"] == "unobservable"


@pytest.mark.parametrize(
    "state,reason",
    [("provider_hidden", "guess"), ("available", "not_recorded"), ("inaccessible", "credential-sentinel")],
)
def test_unknown_annotations_require_fixed_reason_codes(tmp_path, state, reason):
    with pytest.raises(ValueError, match="^invalid configuration selection$"):
        checker().compare_sourced_config(tmp_path, [], {"/provider": {"state": state, "reason": reason}}, {})


def test_json_toml_nested_selectors_preserve_types_and_unknowns(tmp_path):
    module = checker()
    records = [
        artifact(
            tmp_path,
            "d",
            {
                "agent": {"model": "m", "sampling": {"temperature": 0}},
                "run": {"budget": True},
                "task": {"id": "t"},
                "permissions": ["read"],
                "environment": {"x/y~z": "same"},
            },
        ),
        artifact(
            tmp_path,
            "o",
            '[agent]\nmodel="m"\n[agent.sampling]\ntemperature=1\n'
            '[run]\nbudget=1\n[task]\nid="t"\n[environment]\n"x/y~z"="same"\n',
            suffix=".toml",
        ),
    ]
    pointers = {
        "/agent/model": "/agent/model",
        "/agent/sampling/temperature": "/agent/sampling/temperature",
        "/run/budget": "/run/budget",
        "/task": "/task",
        "/tool_permissions": "/permissions/0",
        "/environment": "/environment/x~1y~0z",
    }
    result = module.compare_sourced_config(
        tmp_path, records, {k: ref("d", v) for k, v in pointers.items()}, {k: ref("o", v) for k, v in pointers.items()}
    )
    assert result["differences"] == ["/agent/sampling/temperature", "/run/budget"]
    assert result["unknowns"] == ["/provider", "/runtime_pins", "/tool_permissions"]
    assert result["counts"]["fields"] == 8  # Nested selectors replace their category placeholder.
    assert result["counts"]["compared"] == 5
    assert result["unknown_reasons"]["/tool_permissions"] == {"observed": "pointer_missing"}
    assert result["fields"]["/agent/model"]["observed"]["provenance"]["format"] == "toml"
    assert result["fields"]["/tool_permissions"]["declared"]["state"] == "sourced"


@pytest.mark.parametrize(
    "content,suffix,reason",
    [
        ('{"agent":NaN}', ".json", "invalid_source"),
        ('{"agent":1e999}', ".json", "invalid_source"),
        ('{"agent":1,"agent":2}', ".json", "invalid_source"),
        ("credential-sentinel", ".json", "invalid_source"),
        ("agent = nan", ".toml", "invalid_source"),
        ("agent = 2026-01-01", ".toml", "invalid_source"),
        ('{"agent":"x"}', ".py", "unsupported_format"),
    ],
)
def test_rejects_ambiguous_nonfinite_or_executable_sources(tmp_path, content, suffix, reason):
    module = checker()
    record = artifact(tmp_path, "source", content, suffix=suffix)
    result = module.compare_sourced_config(tmp_path, [record], {"/agent": ref("source", "/agent")}, {})
    assert result["fields"]["/agent"]["declared"]["reason"] == reason
    assert "credential-sentinel" not in json.dumps(result)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("changed", "digest_mismatch"),
        ("missing", "artifact_missing"),
        ("duplicate", "duplicate_artifact"),
        ("denied", "access_denied"),
        ("unresolved", "access_unresolved"),
        ("no_digest", "digest_unresolved"),
        ("symlink", "invalid_source_path"),
        ("directory", "invalid_source_path"),
        ("evidence_changed", "evidence_digest_mismatch"),
        ("no_evidence", "evidence_missing"),
    ],
)
def test_identity_and_source_failures_stay_unknown(tmp_path, mutation, reason):
    module = checker()
    record = artifact(tmp_path, "source", {"agent": "m"})
    records = [record]
    path = tmp_path / record["path"]
    if mutation == "changed":
        path.write_text('{"agent":"changed"}')
    elif mutation == "missing":
        path.unlink()
    elif mutation == "duplicate":
        records.append(dict(record))
    elif mutation in {"denied", "unresolved"}:
        record["access"] = mutation
    elif mutation == "no_digest":
        record["digest"] = None
    elif mutation == "symlink":
        path.rename(tmp_path / "target")
        path.symlink_to(tmp_path / "target")
    elif mutation == "directory":
        path.unlink()
        path.mkdir()
    elif mutation == "evidence_changed":
        evidence = artifact(tmp_path, "evidence", {})
        record["evidence"] = [{"path": evidence["path"], "digest": "0" * 64}]
    elif mutation == "no_evidence":
        record["evidence"] = []
    result = module.compare_sourced_config(tmp_path, records, {"/agent": ref("source", "/agent")}, {})
    assert result["fields"]["/agent"]["declared"]["state"] == "inaccessible"
    assert result["fields"]["/agent"]["declared"]["reason"] == reason


def test_source_reads_are_bounded_and_single_snapshot(tmp_path, monkeypatch):
    module = checker()
    record = artifact(tmp_path, "source", {"agent": "a", "run": "r"})
    reads = []
    real_read = module._read_file

    def read(root, path):
        reads.append(path)
        return real_read(root, path)

    monkeypatch.setattr(module, "_read_file", read)
    refs = {"/agent": ref("source", "/agent"), "/run": ref("source", "/run")}
    module.compare_sourced_config(tmp_path, [record], refs, refs)
    assert reads == ["source.json"]
    monkeypatch.setattr(module, "MAX_SOURCE_BYTES", 8)
    result = module.compare_sourced_config(tmp_path, [record], refs, refs)
    assert result["fields"]["/agent"]["declared"]["reason"] == "source_too_large"


@pytest.mark.parametrize("selector", ["agent", "/invalid", "/agent/~2", "/agent/", 4])
def test_invalid_selectors_are_rejected_safely(tmp_path, selector):
    with pytest.raises(ValueError, match="^invalid configuration selection$"):
        checker().compare_sourced_config(tmp_path, [], {selector: ref("x", "/agent")}, {})


@pytest.mark.parametrize("value", [None, "", "  ", {}, [None], [""], [[], None], {"allow": [], "missing": None}])
def test_nested_unknowns_never_count_as_equal_or_expose_source_keys(tmp_path, value):
    module = checker()
    record = artifact(tmp_path, "source", {"agent": {"secret-key-sentinel": value}})
    refs = {"/agent": ref("source", "/agent")}
    result = module.compare_sourced_config(tmp_path, [record], refs, refs)
    assert result["fields"]["/agent"]["status"] == "unknown"
    assert result["unknown_reasons"]["/agent"] == {"declared": "empty_value", "observed": "empty_value"}
    assert "secret-key-sentinel" not in json.dumps(result)


@pytest.mark.parametrize(
    "binding",
    [
        False,
        {},
        {"run": None},
        {
            "run": {"state": "omitted", "reason": "field_not_selected"},
            "task": ref("task", "/id"),
            "configuration_artifact_id": "d",
            "observations": {},
        },
        {
            "run": ref("run", "/id"),
            "task": ref("task", "/id"),
            "configuration_artifact_id": "d",
            "observations": {"o": {"run": "/id"}},
        },
    ],
)
def test_invalid_binding_is_rejected_before_source_reads(tmp_path, binding):
    with pytest.raises(ValueError, match="^invalid configuration selection$"):
        checker().compare_sourced_config(tmp_path, [], {}, {}, binding=binding)


def test_root_ancestor_swap_never_reads_outside_root(tmp_path, monkeypatch):
    module = checker()
    parent = tmp_path / "parent"
    root = parent / "root"
    root.mkdir(parents=True)
    record = artifact(root, "source", {"agent": "same"})
    outside = tmp_path / "outside"
    (outside / "root").mkdir(parents=True)
    artifact(outside / "root", "source", {"agent": "same"})
    import rubric_identity

    original = rubric_identity.local_file
    reads = []
    real_fdopen = rubric_identity.os.fdopen

    def swap(root, relative):
        path = original(root, relative)
        parent.rename(tmp_path / "retired")
        parent.symlink_to(outside, target_is_directory=True)
        return path

    def track(*args, **kwargs):
        reads.append(True)
        return real_fdopen(*args, **kwargs)

    monkeypatch.setattr(rubric_identity, "local_file", swap)
    monkeypatch.setattr(rubric_identity.os, "fdopen", track)
    result = module.compare_sourced_config(root, [record], {"/agent": ref("source", "/agent")}, {})
    assert result["fields"]["/agent"]["declared"]["reason"] == "invalid_source_path"
    assert reads == []


def test_oversized_evidence_is_rejected_before_identity_copy(tmp_path, monkeypatch):
    module = checker()
    record = artifact(tmp_path, "source", {"agent": "a"})
    record["evidence"] *= module.MAX_FILES + 1
    calls = []
    original = module.identify

    def identify(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(module, "identify", identify)
    result = module.compare_sourced_config(tmp_path, [record], {"/agent": ref("source", "/agent")}, {})
    assert result["fields"]["/agent"]["declared"]["reason"] == "file_limit"
    assert calls == []


@pytest.mark.parametrize("side", ["declared", "observed"])
def test_unsourced_fields_cannot_claim_run_binding(tmp_path, side):
    module = checker()
    records, binding = bound_case(tmp_path)
    declared = {"/agent": ref("declared", "/agent")}
    observed = {"/agent": ref("observed", "/agent")}
    (declared if side == "declared" else observed)["/agent"]["pointer"] = "/missing"
    result = module.compare_sourced_config(tmp_path, records, declared, observed, binding=binding)
    assert result["fields"]["/agent"]["observed"]["binding"] == {
        "status": "unbound",
        "reason": "field_unsourced",
    }
    assert result["counts"]["run_bound"] == 0


@pytest.mark.parametrize("access", [[], {}, True, "credential-sentinel"])
def test_malformed_access_stays_unknown(tmp_path, access):
    record = artifact(tmp_path, "source", {"agent": "a"})
    record["access"] = access
    result = checker().compare_sourced_config(tmp_path, [record], {"/agent": ref("source", "/agent")}, {})
    assert result["fields"]["/agent"]["declared"]["reason"] == "invalid_identity"
    assert "credential-sentinel" not in json.dumps(result)


@pytest.mark.parametrize("unrelated", [None, {}, {"id": "unselected"}])
def test_unselected_malformed_record_does_not_break_binding(tmp_path, unrelated):
    records, binding = bound_case(tmp_path)
    result = checker().compare_sourced_config(
        tmp_path,
        [unrelated, *records],
        {"/agent": ref("declared", "/agent")},
        {"/agent": ref("observed", "/agent")},
        binding=binding,
    )
    assert result["counts"]["run_bound"] == 1


def bound_case(tmp_path):
    run = artifact(tmp_path, "run-record", {"id": "private-run"}, role="run")
    task = artifact(tmp_path, "task-record", {"id": "private-task"}, role="task")
    declared = artifact(tmp_path, "declared", {"agent": "m"})
    observed = artifact(
        tmp_path,
        "observed",
        {"agent": "m", "run": "private-run", "task": "private-task", "configuration_digest": declared["digest"]},
    )
    binding = {
        "run": ref("run-record", "/id"),
        "task": ref("task-record", "/id"),
        "configuration_artifact_id": "declared",
        "observations": {"observed": {"run": "/run", "task": "/task", "configuration_digest": "/configuration_digest"}},
    }
    return [run, task, declared, observed], binding


@pytest.mark.parametrize("mismatch", [None, "run", "task", "configuration_digest"])
def test_empty_permissions_keep_binding_separate_from_literal_equality(tmp_path, mismatch):
    records, binding = bound_case(tmp_path)
    records[2] = artifact(tmp_path, "declared", {"tool_permissions": []})
    observation = {
        "tool_permissions": [],
        "run": "private-run",
        "task": "private-task",
        "configuration_digest": records[2]["digest"],
    }
    if mismatch:
        observation[mismatch] = "mismatch"
    records[3] = artifact(tmp_path, "observed", observation)
    result = checker().compare_sourced_config(
        tmp_path,
        records,
        {"/tool_permissions": ref("declared", "/tool_permissions")},
        {"/tool_permissions": ref("observed", "/tool_permissions")},
        binding=binding,
    )
    field = result["fields"]["/tool_permissions"]
    assert field["status"] == "equal"
    assert field["observed"]["binding"]["status"] == ("unbound" if mismatch else "run_bound")
    assert field["observed"]["binding"]["reason"] == (mismatch + "_mismatch" if mismatch else None)
    assert result["counts"]["equal"] == 1
    assert result["counts"]["run_bound"] == (mismatch is None)
    assert result["acceptable"] is None
    assert result["score"] is None
    assert result["native_attestation"] is False


def test_explicit_run_task_revision_binding_is_not_native_attestation(tmp_path):
    module = checker()
    records, binding = bound_case(tmp_path)
    result = module.compare_sourced_config(
        tmp_path, records, {"/agent": ref("declared", "/agent")}, {"/agent": ref("observed", "/agent")}, binding=binding
    )
    field = result["fields"]["/agent"]
    assert field["observed"]["binding"]["status"] == "run_bound"
    assert field["observed"]["binding"]["basis"] == "selected_record_assertion"
    assert field["observed"]["binding"]["configuration"]["digest"] == records[2]["digest"]
    assert field["declared"]["basis"] == "declaration"
    assert field["observed"]["basis"] == "selected_source_not_native_attestation"
    assert result["counts"]["run_bound"] == 1
    assert result["native_attestation"] is False
    assert result["native_adapter"]["status"] == "unsupported"
    assert "config" in result["native_adapter"]["reason"]
    assert result["score"] is None
    assert "private-" not in json.dumps(result)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("run", "run_mismatch"),
        ("task", "task_mismatch"),
        ("configuration_digest", "configuration_digest_mismatch"),
        ("missing_observation", "observation_binding_missing"),
        ("wrong_role", "binding_role_invalid"),
        ("wrong_declaration", "declaration_configuration_mismatch"),
        ("missing_record", "binding_source_unavailable"),
    ],
)
def test_bindings_fail_closed_without_blocking_literal_comparison(tmp_path, mutation, reason):
    module = checker()
    records, binding = bound_case(tmp_path)
    declared = {"/agent": ref("declared", "/agent")}
    if mutation in {"run", "task", "configuration_digest"}:
        data = json.loads((tmp_path / "observed.json").read_bytes())
        data[mutation] = "mismatch"
        records[-1] = artifact(tmp_path, "observed", data)
    elif mutation == "missing_observation":
        binding["observations"] = {}
    elif mutation == "wrong_role":
        records[0]["role"] = "configuration"
    elif mutation == "wrong_declaration":
        declared = {"/agent": ref("observed", "/agent")}
    elif mutation == "missing_record":
        records.pop(0)
    result = module.compare_sourced_config(
        tmp_path, records, declared, {"/agent": ref("observed", "/agent")}, binding=binding
    )
    assert result["fields"]["/agent"]["status"] == "equal"
    actual = result["fields"]["/agent"]["observed"]["binding"]
    assert actual["status"] == "unbound"
    assert actual["reason"] == reason
    assert result["counts"]["run_bound"] == 0
    assert result["native_attestation"] is False
