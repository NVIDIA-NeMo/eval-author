# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import importlib.util
import json
import subprocess
import sys
import textwrap
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


def test_identity_accepts_trusted_root_alias_but_not_child_alias(tmp_path):
    checker = load_checker()
    root = tmp_path / "physical"
    root.mkdir()
    (root / "source.json").write_bytes(b"{}")
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    record = selected_record("source.json", b"{}")
    assert checker.identify(alias, [record])["status"] == "pass"
    (root / "child.json").symlink_to(root / "source.json")
    assert checker.identify(alias, [selected_record("child.json", b"{}")])["status"] == "fail"


def test_public_reader_and_selection_envelope(tmp_path):
    checker = load_checker()
    (tmp_path / "source.json").write_bytes(b"{}")
    assert checker.read_selected(tmp_path, "source.json") == (b"{}", None)
    validate = checker.validate_identity_selection
    valid = {"artifacts": [], "route": {}}
    assert validate(valid) is None
    for invalid in (
        None,
        [],
        {},
        {**valid, "extra": True},
        {"artifacts": {}, "route": {}},
        {"artifacts": [], "route": []},
    ):
        with pytest.raises(ValueError):
            validate(invalid)


def test_failed_opens_do_not_exhaust_total_byte_budget(tmp_path):
    checker = load_checker()
    records = [selected_record(f"missing-{i}.json", b"{}", f"missing-{i}") for i in range(17)]
    (tmp_path / "valid.json").write_bytes(b"{}")
    records.append(selected_record("valid.json", b"{}", "valid"))
    result = checker.identify(tmp_path, records)
    assert not any(row["subject"] == "valid" for row in result["diagnostics"])


@pytest.mark.parametrize("role,expected", [("trace", "pass"), ("configuration", "unknown")])
def test_explicit_trace_budget_is_shared_with_identity(tmp_path, role, expected):
    checker = load_checker()
    data = b" " * (checker.MAX_SOURCE_BYTES + 1)
    (tmp_path / "large.json").write_bytes(data)
    record = dict(selected_record("large.json", data), role=role)
    assert checker.identify(tmp_path, [record])["status"] == expected
    if role == "trace":
        assert checker.artifact_byte_limit(record) == 2_000_000


@pytest.mark.parametrize("consumer", ["configuration", "native"])
def test_consumers_reject_parent_replacement_during_read(tmp_path, monkeypatch, consumer):
    import os

    import rubric_configuration
    import rubric_native

    parent = tmp_path / "selected"
    parent.mkdir()
    (parent / "file.json").write_bytes(b"{}")
    original = os.fstat
    calls = 0

    def replaced(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            parent.rename(tmp_path / "old-parent")
            parent.mkdir()
            (parent / "file.json").write_bytes(b"{}")
        return original(descriptor)

    monkeypatch.setattr(os, "fstat", replaced)
    if consumer == "configuration":
        assert rubric_configuration._read_file(tmp_path, "selected/file.json") == (None, "source_changed")
    else:
        with pytest.raises(ValueError):
            rubric_native._read(tmp_path, selected_record("selected/file.json", b"{}"), 100)


@pytest.mark.parametrize("pointer", ["", "/items/0", "/~0~1", "/bad~2", "/" + "x" * 1024, "/bad\nkey", "/items/00"])
def test_pointer_consumers_share_strict_syntax(pointer):
    import rubric_configuration
    import rubric_freshness

    checker = load_checker()
    value = {"items": [3], "~/": 4, "bad\nkey": 5, "x" * 1024: 6}
    results = []
    for resolve in (checker._route_pointer, rubric_freshness._binding_pointer):
        try:
            results.append((resolve(value, pointer), True))
        except (ValueError, KeyError):
            results.append((None, False))
    try:
        results.append(rubric_configuration._resolve(value, pointer))
    except ValueError:
        results.append((None, False))
    assert results[0] == results[1] == results[2]


def test_tree_consumers_reject_non_json_object_keys():
    import rubric_configuration

    with pytest.raises(ValueError):
        rubric_configuration._finite_tree({1: "invalid"})


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


@pytest.mark.parametrize("access", ["missing", "denied", "unresolved"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("snapshot", [False, True])
def test_identity_never_reads_selected_unavailable_aliases(tmp_path, monkeypatch, access, reverse, direct, snapshot):
    checker = load_checker()
    data = b"PRIVATE_SENTINEL"
    (tmp_path / "private.bin").write_bytes(data)
    (tmp_path / "neighbor.bin").write_bytes(b"neighbor")
    unavailable = selected_record("private.bin", data, "unavailable")
    unavailable["access"] = access
    alias = (
        selected_record("private.bin", data, "alias")
        if direct
        else selected_record("neighbor.bin", b"neighbor", "alias")
    )
    alias["evidence"] = [{"path": "private.bin", "digest": unavailable["digest"]}]
    neighbor = selected_record("neighbor.bin", b"neighbor", "neighbor")
    records = [alias, neighbor, unavailable] if reverse else [unavailable, neighbor, alias]
    calls = []
    original = checker._read_selected

    def read(root, relative, limit):
        calls.append(relative)
        return original(root, relative, limit)

    def hasher(path):
        calls.append(path.name)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    monkeypatch.setattr(checker, "_read_selected", read)
    result = checker.identify(tmp_path, records, hasher=hasher if snapshot else None)
    assert calls == ["neighbor.bin"]
    assert result["status"] == "unknown"
    assert result["records"] == records
    assert result["diagnostics"] == [
        {"code": "identity.access_" + access, "subject": subject, "status": "unknown"}
        for subject in ("alias", "unavailable")
    ]


@pytest.mark.parametrize("mutation", ["extra", "evidence", "digest", "limit"])
def test_malformed_unavailable_metadata_cannot_restore_alias_access(tmp_path, monkeypatch, mutation):
    checker = load_checker()
    unavailable = selected_record("private.bin", b"private", "unavailable")
    unavailable["access"] = "denied"
    if mutation == "extra":
        unavailable["extra"] = True
    elif mutation == "evidence":
        unavailable["evidence"] = None
    elif mutation == "digest":
        unavailable["digest"] = []
    else:
        unavailable["evidence"] *= checker.MAX_REFERENCES + 1

    def unexpected(*args):
        pytest.fail("unavailable selection reached the filesystem")

    monkeypatch.setattr(checker, "local_file", unexpected)
    result = checker.identify(tmp_path, [selected_record("private.bin", b"private", "alias"), unavailable])
    assert {"code": "identity.access_denied", "subject": "alias", "status": "unknown"} in result["diagnostics"]
    assert result["status"] != "pass"


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_unavailable_aliases_have_stable_diagnostics(tmp_path, monkeypatch, reverse):
    checker = load_checker()
    records = [selected_record("private.bin", b"private", access) for access in ("missing", "denied", "unresolved")]
    for record in records:
        record["access"] = record["id"]
    records.append(selected_record("private.bin", b"private", "alias"))
    if reverse:
        records.reverse()

    def unexpected(*args):
        pytest.fail("unavailable selection reached the filesystem")

    monkeypatch.setattr(checker, "local_file", unexpected)
    result = checker.identify(tmp_path, records)
    assert result["status"] == "unknown"
    assert result["diagnostics"] == [
        {"code": "identity.access_" + access, "subject": subject, "status": "unknown"}
        for subject, access in (
            ("alias", "denied"),
            ("denied", "denied"),
            ("missing", "missing"),
            ("unresolved", "unresolved"),
        )
    ]


@pytest.mark.parametrize(
    "path",
    [None, True, 7, {}, [], "", ".", "./private", "a/../private", "a//private", "/private", "private/", "private\x00"],
)
@pytest.mark.parametrize("access", ["available", "denied"])
@pytest.mark.parametrize("evidence_only", [False, True])
def test_identity_validates_relative_references_without_filesystem_access(
    tmp_path, monkeypatch, path, access, evidence_only
):
    checker = load_checker()
    record = selected_record("private", b"private")
    record["access"] = access
    if evidence_only:
        record["evidence"][0]["path"] = path
    else:
        record["path"] = path

    def unexpected(*args):
        pytest.fail("invalid reference reached the filesystem")

    monkeypatch.setattr(checker, "local_file", unexpected)
    result = checker.identify(tmp_path, [record])
    assert result["records"] == []
    assert result["diagnostics"] == [{"code": "identity.reference_invalid", "subject": "selected", "status": "fail"}]


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


def test_raw_route_is_explicitly_unbound(tmp_path, route_selection):
    entry = next(row for row in route_selection["artifacts"] if row["id"] == route_selection["route"]["entrypoint"])
    path = tmp_path / entry["path"]
    dispatch = json.loads(path.read_bytes())
    dispatch.pop("dependencies", None)
    raw = json.dumps(dispatch).encode()
    path.write_bytes(raw)
    entry["digest"] = hashlib.sha256(raw).hexdigest()
    entry["evidence"] = [{"path": entry["path"], "digest": entry["digest"]}]
    route_selection["route"].pop("dependency_source", None)
    result = load_checker().resolve_route(tmp_path, route_selection["artifacts"], route_selection["route"])
    assert result["status"] == "pass"
    assert result["bound_dependency"] is False
    assert result["dependency_source"] is None


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

    def changed_after_identity(root, records, **kwargs):
        result = original(root, records, **kwargs)
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


@pytest.mark.parametrize("limit_kind", ["files", "bytes", "records", "evidence"])
def test_identity_bounds_whole_selection_before_extra_reads(tmp_path, monkeypatch, limit_kind):
    checker = load_checker()
    monkeypatch.setattr(checker, "MAX_FILES", 2, raising=False)
    monkeypatch.setattr(checker, "MAX_TOTAL_BYTES", 4, raising=False)
    monkeypatch.setattr(checker, "MAX_RECORDS", 3, raising=False)
    monkeypatch.setattr(checker, "MAX_REFERENCES", 3, raising=False)
    records = []
    for index in range(4):
        name = f"{index}.json"
        (tmp_path / name).write_bytes(b"{}")
        records.append(selected_record(name, b"{}", str(index)))
    if limit_kind == "bytes":
        monkeypatch.setattr(checker, "MAX_FILES", 8)
    if limit_kind == "records":
        expected = "selection_limit"
    elif limit_kind == "evidence":
        records[0]["evidence"] *= 4
        records = records[:1]
        expected = "reference_limit"
    else:
        records = records[:3]
        expected = "file_limit" if limit_kind == "files" else "total_bytes_limit"
    result = checker.identify(tmp_path, records)
    assert result["status"] == "unknown"
    assert any(row["code"] == "identity." + expected for row in result["diagnostics"])


def test_identity_reuses_exact_bytes_for_duplicate_references(tmp_path, monkeypatch):
    checker = load_checker()
    data = b"\xff\x00\r\n"
    (tmp_path / "selected.bin").write_bytes(data)
    monkeypatch.setattr(checker, "MAX_TOTAL_BYTES", len(data), raising=False)
    calls = []
    original = checker._read_selected

    def counted(*args):
        calls.append(args[1])
        return original(*args)

    monkeypatch.setattr(checker, "_read_selected", counted)
    record = selected_record("selected.bin", data)
    assert checker.identify(tmp_path, [record])["status"] == "pass"
    assert calls == ["selected.bin"]


def test_identity_accepts_injected_snapshot_hasher(tmp_path):
    checker = load_checker()
    record = selected_record("not-on-disk", b"snapshot")
    calls = []

    def hasher(path):
        calls.append(path)
        return record["digest"]

    assert checker.identify(tmp_path, [record], hasher=hasher)["status"] == "pass"
    assert calls == [tmp_path / "not-on-disk"]


@pytest.mark.parametrize("replacement", ["small", "oversized", "symlink"])
def test_dispatch_reread_is_bounded_and_nofollow(tmp_path, monkeypatch, replacement):
    checker = load_checker()
    data = b'{"selector":"strict","routes":{"strict":"entry"}}'
    path = tmp_path / "entry.json"
    path.write_bytes(data)
    record = selected_record(path.name, data, "entry")
    record["role"] = "scorer"
    original = checker.identify

    def changed(root, records, **kwargs):
        result = original(root, records, **kwargs)
        if replacement == "oversized":
            path.write_bytes(b"x" * (checker.MAX_SOURCE_BYTES + 1))
        elif replacement == "symlink":
            target = tmp_path / "private.json"
            target.write_bytes(data)
            path.unlink()
            path.symlink_to(target)
        return result

    def unbounded(path):
        pytest.fail("unbounded dispatch read")

    monkeypatch.setattr(checker, "identify", changed)
    monkeypatch.setattr(Path, "read_bytes", unbounded)
    result = checker.resolve_route(
        tmp_path,
        [record],
        {
            "entrypoint": "entry",
            "expected_scorer": "entry",
            "dependencies": {"entry": []},
        },
    )
    assert result["status"] == ("pass" if replacement == "small" else "unknown")
    if replacement != "small":
        assert result["diagnostics"] == [
            {
                "code": "route.dispatch_unresolved",
                "subject": "entry",
                "status": "unknown",
            }
        ]


def test_identity_rejects_replaced_source_during_read(tmp_path, monkeypatch):
    checker = load_checker()
    data = b"original"
    path = tmp_path / "selected.bin"
    path.write_bytes(data)
    original = checker.os.fstat
    calls = 0

    def replaced(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.rename(tmp_path / "old-source")
            path.write_bytes(b"replaced")
        return original(descriptor)

    monkeypatch.setattr(checker.os, "fstat", replaced)
    result = checker.identify(tmp_path, [selected_record(path.name, data)])
    assert result["status"] == "unknown"
    assert result["diagnostics"] == [
        {
            "code": "identity.source_changed",
            "subject": "selected",
            "status": "unknown",
        }
    ]


@pytest.mark.parametrize("race", ["file", "parent"])
def test_identity_nofollow_survives_path_check_race(tmp_path, monkeypatch, race):
    checker = load_checker()
    directory = tmp_path / "selected"
    directory.mkdir()
    data = b"PRIVATE_SENTINEL"
    path = directory / "file"
    path.write_bytes(data)
    target = tmp_path / "target"
    target.mkdir()
    (target / "file").write_bytes(data)
    original = checker.local_file

    def swapped(root, relative):
        checked = original(root, relative)
        if race == "file":
            path.unlink()
            path.symlink_to(target / "file")
        else:
            directory.rename(tmp_path / "old")
            directory.symlink_to(target, target_is_directory=True)
        return checked

    monkeypatch.setattr(checker, "local_file", swapped)
    result = checker.identify(tmp_path, [selected_record("selected/file", data)])
    assert result["status"] == "fail"
    assert result["diagnostics"] == [
        {
            "code": "identity.reference_invalid",
            "subject": "selected",
            "status": "fail",
        }
    ]
    assert "PRIVATE_SENTINEL" not in json.dumps(result)


@pytest.mark.parametrize("role", ["task", "scorer", "configuration", "manifest", "custom-role"])
def test_identity_preserves_roles_and_exact_file_limit(tmp_path, monkeypatch, role):
    checker = load_checker()
    data = b"\xff\r\n\x00"
    path = tmp_path / "selected.bin"
    path.write_bytes(data)
    record = selected_record(path.name, data)
    record["role"] = role
    monkeypatch.setattr(checker, "MAX_SOURCE_BYTES", len(data))
    assert checker.identify(tmp_path, [record])["records"] == [record]
    assert checker.identify(tmp_path, [record])["status"] == "pass"
    path.write_bytes(data + b"x")
    result = checker.identify(tmp_path, [record])
    assert result["diagnostics"] == [
        {
            "code": "identity.source_too_large",
            "subject": "selected",
            "status": "unknown",
        }
    ]


@pytest.mark.parametrize("source", ["missing", "directory", "fifo"])
def test_identity_preserves_missing_and_nonregular_diagnostics(tmp_path, source):
    checker = load_checker()
    path = tmp_path / "selected"
    if source == "directory":
        path.mkdir()
    elif source == "fifo":
        checker.os.mkfifo(path)
    result = checker.identify(tmp_path, [selected_record(path.name, b"")])
    assert result["diagnostics"] == [
        {
            "code": "identity.artifact_missing" if source == "missing" else "identity.reference_invalid",
            "subject": "selected",
            "status": "unknown" if source == "missing" else "fail",
        }
    ]


@pytest.mark.parametrize("replacement", ["rename", "symlink"])
def test_identity_rejects_parent_replacement_during_read(tmp_path, monkeypatch, replacement):
    checker = load_checker()
    parent = tmp_path / "selected"
    parent.mkdir()
    path = parent / "file"
    path.write_bytes(b"original")
    original = checker.os.fstat
    calls = 0

    def replaced(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            parent.rename(tmp_path / "old-parent")
            if replacement == "rename":
                parent.mkdir()
                (parent / "file").write_bytes(b"replaced")
            else:
                parent.symlink_to(tmp_path / "old-parent", target_is_directory=True)
        return original(descriptor)

    monkeypatch.setattr(checker.os, "fstat", replaced)
    result = checker.identify(tmp_path, [selected_record("selected/file", b"original")])
    assert result["diagnostics"] == [
        {
            "code": "identity.source_changed",
            "subject": "selected",
            "status": "unknown",
        }
    ]


@pytest.fixture
def bound_route_selection(tmp_path):
    graph = {"scorer": ["import"], "import": ["prompt"], "prompt": ["input"], "input": []}
    documents = {
        "entry": {"selector": "strict", "routes": {"strict": "scorer", "unused": "other"}, "dependencies": graph},
        "graph": {"nested/key": {"~graph": graph}},
        "scorer": {},
        "import": {},
        "prompt": {},
        "input": {},
    }
    records = []
    for key, value in documents.items():
        data = json.dumps(value).encode()
        (tmp_path / (key + ".json")).write_bytes(data)
        record = selected_record(key + ".json", data, key)
        record["role"] = key
        records.append(record)
    return records, {"entrypoint": "entry", "expected_scorer": "scorer", "dependencies": graph}


@pytest.mark.parametrize("explicit", [False, True])
def test_route_binds_retained_transitive_json_graph(tmp_path, bound_route_selection, explicit):
    records, route = bound_route_selection
    source = (
        {"artifact_id": "graph", "pointer": "/nested~1key/~0graph"}
        if explicit
        else {
            "artifact_id": "entry",
            "pointer": "/dependencies",
        }
    )
    if explicit:
        route["dependency_source"] = source
    result = load_checker().resolve_route(tmp_path, records, route)
    assert result["status"] == "pass"
    assert result["diagnostics"] == []
    assert result["selected"] == ["import", "input", "prompt", "scorer"]
    assert result["bound_dependency"] is True
    assert result["dependency_source"] == {
        **source,
        "digest": next(row["digest"] for row in records if row["id"] == source["artifact_id"]),
    }
    assert result["executed_scorer"] is None


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("mutation", ["omit_edge", "omit_node", "replace_edge", "type_confusion"])
def test_bound_route_rejects_caller_only_graph_mutation(tmp_path, bound_route_selection, explicit, mutation):
    records, route = bound_route_selection
    if explicit:
        route["dependency_source"] = {"artifact_id": "graph", "pointer": "/nested~1key/~0graph"}
    if mutation == "omit_edge":
        route["dependencies"]["scorer"] = []
    elif mutation == "omit_node":
        del route["dependencies"]["input"]
    elif mutation == "replace_edge":
        route["dependencies"]["scorer"] = ["prompt"]
    else:
        # Python equality must not admit bool/int substitution in unused data.
        document = json.loads((tmp_path / "entry.json").read_bytes())
        document["dependencies"]["other"] = [True]
        refresh_route_document(tmp_path, records, "entry", document)
        refresh_route_document(tmp_path, records, "graph", {"nested/key": {"~graph": document["dependencies"]}})
        route["dependencies"]["other"] = [1]
    result = load_checker().resolve_route(tmp_path, records, route)
    assert result["status"] == "fail"
    assert result["bound_dependency"] is False
    assert {
        "code": "route.dependency_source_mismatch",
        "subject": "graph" if explicit else "entry",
        "status": "fail",
    } in result["diagnostics"]


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("bad", ["cycle", "duplicate"])
@pytest.mark.parametrize("selected", [False, True])
def test_route_rejects_only_selected_bad_edges(tmp_path, bound_route_selection, bound, bad, selected):
    records, route = bound_route_selection
    graph = route["dependencies"]
    key = "input" if selected else "other"
    graph[key] = ["scorer"] if bad == "cycle" else ["prompt", "prompt"]
    if not selected and bad == "cycle":
        graph[key] = [key]
    document = json.loads((tmp_path / "entry.json").read_bytes())
    if bound:
        document["dependencies"] = graph
    else:
        del document["dependencies"]
    refresh_route_document(tmp_path, records, "entry", document)
    result = load_checker().resolve_route(tmp_path, records, route)
    assert result["status"] == ("fail" if selected else "pass")
    assert result["bound_dependency"] is (bound and not selected)
    assert result["selected"] == ["import", "input", "prompt", "scorer"]
    if selected:
        assert any(row["code"] == "route.dependency_" + bad for row in result["diagnostics"])
    else:
        assert result["diagnostics"] == []


@pytest.mark.parametrize("other", [None, "dynamic import", ["missing"], [False]])
def test_bound_route_ignores_unselected_malformed_branch(tmp_path, bound_route_selection, other):
    records, route = bound_route_selection
    route["dependencies"]["other"] = other
    document = json.loads((tmp_path / "entry.json").read_bytes())
    document["dependencies"] = route["dependencies"]
    refresh_route_document(tmp_path, records, "entry", document)
    result = load_checker().resolve_route(tmp_path, records, route)
    assert result["status"] == "pass"
    assert result["bound_dependency"] is True
    assert "other" not in result["selected"]


@pytest.mark.parametrize("bad", ["tuple", "integer_key", "oversized_edges", "oversized_closure"])
def test_route_rejects_unsupported_or_unbounded_graph(tmp_path, bound_route_selection, monkeypatch, bad):
    records, route = bound_route_selection
    checker = load_checker()
    if bad == "tuple":
        route["dependencies"]["scorer"] = ("import",)
    elif bad == "integer_key":
        route["dependencies"] = {1: []}
        refresh_route_document(tmp_path, records, "graph", {"1": []})
        route["dependency_source"] = {"artifact_id": "graph", "pointer": ""}
    elif bad == "oversized_edges":
        monkeypatch.setattr(checker, "MAX_REFERENCES", 1)
        route["dependencies"]["scorer"] = ["import", "input"]
        document = json.loads((tmp_path / "entry.json").read_bytes())
        document["dependencies"] = route["dependencies"]
        refresh_route_document(tmp_path, records, "entry", document)
    else:
        monkeypatch.setattr(checker, "MAX_RECORDS", 2)
    result = checker.resolve_route(tmp_path, records, route)
    assert result["status"] != "pass"
    assert result["bound_dependency"] is False


@pytest.mark.parametrize("replacement", ["different", "oversized", "symlink"])
def test_dependency_source_reread_is_hash_bound_bounded_nofollow(
    tmp_path, bound_route_selection, monkeypatch, replacement
):
    records, route = bound_route_selection
    route["dependency_source"] = {"artifact_id": "graph", "pointer": "/nested~1key/~0graph"}
    checker = load_checker()
    original = checker.identify
    path = tmp_path / "graph.json"

    def changed(root, selected, **kwargs):
        result = original(root, selected, **kwargs)
        if selected[0]["id"] == "graph":
            if replacement == "different":
                path.write_text("{}")
            elif replacement == "oversized":
                path.write_bytes(b"x" * (checker.MAX_SOURCE_BYTES + 1))
            else:
                path.rename(tmp_path / "private.json")
                path.symlink_to(tmp_path / "private.json")
        return result

    monkeypatch.setattr(checker, "identify", changed)
    result = checker.resolve_route(tmp_path, records, route)
    assert result["bound_dependency"] is False
    code = "route.dependency_source_changed" if replacement == "different" else "route.dependency_source_unresolved"
    assert result["diagnostics"] == [
        {"code": code, "subject": "graph", "status": "fail" if replacement == "different" else "unknown"}
    ]


@pytest.mark.parametrize(
    "source",
    [
        None,
        {},
        {"artifact_id": "graph", "pointer": "/bad~2pointer"},
        {"artifact_id": "graph", "pointer": "/missing"},
        {"artifact_id": "graph", "pointer": "/nested~1key/~0graph/scorer/00"},
    ],
)
def test_invalid_explicit_dependency_source_never_falls_back(tmp_path, bound_route_selection, source):
    records, route = bound_route_selection
    route["dependency_source"] = source
    result = load_checker().resolve_route(tmp_path, records, route)
    assert result["status"] == "fail"
    assert result["bound_dependency"] is False
    assert any(row["code"] == "route.dependency_source_invalid" for row in result["diagnostics"])


@pytest.mark.parametrize("unsupported", ["tuple", "integer_key"])
def test_bound_route_does_not_coerce_python_values_to_json(tmp_path, bound_route_selection, unsupported):
    records, route = bound_route_selection
    document = json.loads((tmp_path / "entry.json").read_bytes())
    if unsupported == "tuple":
        document["dependencies"]["other"] = ["input"]
        route["dependencies"]["other"] = ("input",)
    else:
        document["dependencies"]["other"] = {"1": []}
        route["dependencies"]["other"] = {1: []}
    refresh_route_document(tmp_path, records, "entry", document)
    result = load_checker().resolve_route(tmp_path, records, route)
    assert result["status"] == "fail"
    assert result["bound_dependency"] is False


@pytest.mark.parametrize("source", ["entry", "graph"])
@pytest.mark.parametrize(
    "data",
    [
        b'{"scorer": [], "scorer": ["input"]}',
        b'{"scorer": NaN}',
        b'__import__("os").system("false")',
        b"[" * 2000 + b"]" * 2000,
    ],
)
def test_route_only_parses_supported_json_sources(tmp_path, bound_route_selection, source, data):
    records, route = bound_route_selection
    if source == "graph":
        route["dependency_source"] = {"artifact_id": "graph", "pointer": ""}
    record = next(row for row in records if row["id"] == source)
    (tmp_path / record["path"]).write_bytes(data)
    record["digest"] = record["evidence"][0]["digest"] = hashlib.sha256(data).hexdigest()
    result = load_checker().resolve_route(tmp_path, records, route)
    assert result["status"] != "pass"
    assert result["bound_dependency"] is False


@pytest.mark.parametrize("key", ["graph", "scorer", "import", "prompt", "input"])
def test_bound_route_rejects_mutated_retained_source(tmp_path, bound_route_selection, key):
    records, route = bound_route_selection
    route["dependency_source"] = {"artifact_id": "graph", "pointer": "/nested~1key/~0graph"}
    (tmp_path / (key + ".json")).write_text("changed without updating identity")
    result = load_checker().resolve_route(tmp_path, records, route)
    assert result["status"] == "fail"
    assert result["bound_dependency"] is False
    assert {"code": "identity.digest_mismatch", "subject": key, "status": "fail"} in result["diagnostics"]


def test_bound_route_allows_shared_transitive_inputs(tmp_path, bound_route_selection):
    records, route = bound_route_selection
    route["dependencies"]["scorer"] = ["input", "import"]
    document = json.loads((tmp_path / "entry.json").read_bytes())
    document["dependencies"] = route["dependencies"]
    refresh_route_document(tmp_path, records, "entry", document)
    result = load_checker().resolve_route(tmp_path, records, route)
    assert result["status"] == "pass"
    assert result["bound_dependency"] is True
    assert result["selected"] == ["import", "input", "prompt", "scorer"]


def test_route_dispatch_parser_recursion_is_unresolved(tmp_path, bound_route_selection, monkeypatch):
    records, route = bound_route_selection
    checker = load_checker()

    def recursion(data):
        raise RecursionError("source exceeds parser depth")

    monkeypatch.setattr(checker, "loads", recursion)
    result = checker.resolve_route(tmp_path, records, route)
    assert result["status"] == "unknown"
    assert result["bound_dependency"] is False
    assert result["diagnostics"] == [{"code": "route.dispatch_unresolved", "subject": "entry", "status": "unknown"}]


def refresh_route_document(root, records, key, value):
    record = next(row for row in records if row["id"] == key)
    data = json.dumps(value).encode()
    (root / record["path"]).write_bytes(data)
    record["digest"] = record["evidence"][0]["digest"] = hashlib.sha256(data).hexdigest()


def selected_record(path, data, ident="selected"):
    sha = hashlib.sha256(data).hexdigest()
    return {
        "id": ident,
        "role": "configuration",
        "path": path,
        "digest": sha,
        "revision": None,
        "access": "available",
        "selection_origin": "explicit",
        "source": "synthetic",
        "evidence": [{"path": path, "digest": sha}],
    }


@pytest.mark.parametrize("access", ["missing", "denied", "unresolved"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("direct", [False, True])
def test_cli_identity_never_opens_selected_unavailable_alias(tmp_path, access, reverse, direct):
    data = b"PRIVATE_SENTINEL"
    (tmp_path / "private.bin").write_bytes(data)
    (tmp_path / "neighbor.bin").write_bytes(b"neighbor")
    unavailable = selected_record("private.bin", data, "unavailable")
    unavailable["access"] = access
    alias = (
        selected_record("private.bin", data, "alias")
        if direct
        else selected_record("neighbor.bin", b"neighbor", "alias")
    )
    alias["evidence"] = [{"path": "private.bin", "digest": unavailable["digest"]}]
    artifacts = [alias, unavailable] if reverse else [unavailable, alias]
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"artifacts": artifacts}))
    wrapper = textwrap.dedent("""\
        import pathlib, runpy, sys
        def guard(event, args):
            if event == "open" and isinstance(args[0], str):
                assert pathlib.PurePath(args[0]).name != "private.bin", "unavailable source opened"
        sys.addaudithook(guard)
        sys.argv = sys.argv[1:]
        sys.path.insert(0, str(pathlib.Path(sys.argv[0]).parent))
        runpy.run_path(sys.argv[0], run_name="__main__")
    """)
    cli = ROOT / "skills/eval-author-rubric/scripts/measure.py"
    result = subprocess.run(
        [sys.executable, "-B", "-c", wrapper, str(cli), "--root", str(tmp_path), "--selection", str(selection)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.stderr == ""
    assert result.returncode == 1
    assert "PRIVATE_SENTINEL" not in result.stdout
    identity = json.loads(result.stdout)["measurements"]["identity"]
    assert identity["status"] == "unknown"
    assert identity["diagnostics"] == [
        {"code": "identity.access_" + access, "subject": subject, "status": "unknown"}
        for subject in ("alias", "unavailable")
    ]


@pytest.mark.parametrize("consumer", ["route", "sourced_config"])
@pytest.mark.parametrize("access", ["missing", "denied", "unresolved"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("direct", [False, True])
def test_cli_readers_never_open_selected_unavailable_alias(tmp_path, consumer, access, reverse, direct):
    data = (
        b'{"selector":"default","routes":{"default":"scorer"},"dependencies":{"scorer":[]},"agent":"PRIVATE_SENTINEL"}'
    )
    (tmp_path / "private.json").write_bytes(data)
    (tmp_path / "alias.json").write_bytes(data)
    (tmp_path / "neighbor.json").write_bytes(b'{"agent":"neighbor"}')
    unavailable = dict(selected_record("private.json", data, "unavailable"), access=access)
    alias = selected_record("private.json" if direct else "alias.json", data, "alias")
    alias["evidence"] = [{"path": "private.json", "digest": unavailable["digest"]}]
    neighbor = selected_record("neighbor.json", b'{"agent":"neighbor"}', "scorer")
    neighbor["role"] = "scorer"
    artifacts = [alias, unavailable, neighbor] if reverse else [unavailable, alias, neighbor]
    if consumer == "route":
        choice = {"entrypoint": "alias", "expected_scorer": "scorer", "dependencies": {"scorer": []}}
    else:
        fields = {
            "/agent": {"artifact_id": "alias", "pointer": "/agent"},
            "/provider": {"artifact_id": "scorer", "pointer": "/agent"},
        }
        choice = {"declared": fields, "observed": fields}
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"artifacts": artifacts, consumer: choice}))
    result = audited_rubric_cli(tmp_path, selection, "private.json")
    assert result.stderr == ""
    assert result.returncode == 1
    assert "PRIVATE_SENTINEL" not in result.stdout
    measurements = json.loads(result.stdout)["measurements"]
    assert len(measurements["identity"]["records"]) == 3
    assert not any(row["subject"] == "scorer" for row in measurements["identity"]["diagnostics"])
    report = measurements[consumer]
    assert report["status"] == "unknown"
    if consumer == "route":
        assert report["bound_dependency"] is False
        assert report["diagnostics"] == [{"code": "identity.access_" + access, "subject": "alias", "status": "unknown"}]
    else:
        assert report["fields"]["/agent"]["status"] == "unknown"
        assert report["fields"]["/provider"]["status"] == "equal"
        assert report["counts"]["compared"] == 1


@pytest.mark.parametrize("consumer", ["approvals", "native", "scoring"])
@pytest.mark.parametrize("access", ["missing", "denied", "unresolved"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("direct", [False, True])
def test_cli_downstream_readers_respect_full_identity_selection(tmp_path, consumer, access, reverse, direct):
    from test_rubric_approval_sources import fixture

    approvals, decisions, identity, sources = fixture(tmp_path)
    records = identity["records"]
    selected = records[-1]
    choice = {"approvals": approvals, "decisions": decisions, "approval_sources": sources}
    if consumer == "native":
        choice = {"native": {"receipt_ids": [], "manifest_ids": [], "result_ids": [], "trace_ids": [selected["id"]]}}
    elif consumer == "scoring":
        choice = {
            "rubric_scope": {
                "task_artifacts": {"source": selected["id"], "required": ["task"], "unresolved_material": []}
            }
        }
    private = tmp_path / "private.json"
    private.write_bytes((tmp_path / selected["path"]).read_bytes())
    records.append(dict(selected, id="unavailable", path=private.name, access=access))
    selected["evidence"] = [{"path": private.name, "digest": selected["digest"]}]
    if direct:
        selected["path"] = private.name
    if reverse:
        records.reverse()
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"artifacts": records, **choice}))
    completed = audited_rubric_cli(tmp_path, selection, private.name)
    assert completed.stderr == ""
    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    assert len(report["measurements"]["identity"]["records"]) == len(records)
    assert report["measurements"]["identity"]["status"] == "unknown"
    if consumer == "approvals":
        assert report["measurements"]["approvals"]["evidence"] == []
        assert report["measurements"]["approvals"]["status"] != "pass"
    elif consumer == "native":
        assert report["measurements"]["native"]["bindings"] == []
        assert report["measurements"]["native"]["status"] != "pass"
    else:
        assert report["items"]["DISC-01-A"]["score"] is None


def audited_rubric_cli(root, selection, forbidden):
    wrapper = textwrap.dedent("""\
        import pathlib, runpy, sys
        forbidden = sys.argv.pop(1)
        def guard(event, args):
            if event == "open" and isinstance(args[0], str):
                assert pathlib.PurePath(args[0]).name != forbidden, "unavailable source opened"
        sys.addaudithook(guard)
        sys.argv = sys.argv[1:]
        sys.path.insert(0, str(pathlib.Path(sys.argv[0]).parent))
        runpy.run_path(sys.argv[0], run_name="__main__")
    """)
    cli = ROOT / "skills/eval-author-rubric/scripts/measure.py"
    return subprocess.run(
        [sys.executable, "-B", "-c", wrapper, forbidden, str(cli), "--root", str(root), "--selection", str(selection)],
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.parametrize("evidence_only", [False, True])
@pytest.mark.parametrize("unsafe", ["oversized", "symlink", "ancestor_symlink"])
def test_cli_rejects_large_selected_sources_before_unbounded_reads(tmp_path, evidence_only, unsafe):
    data = b'{"agent":"PRIVATE_SENTINEL' + b"x" * 1_100_000 + b'"}'
    (tmp_path / "large.json").write_bytes(data)
    record = selected_record("large.json", data)
    if unsafe == "symlink":
        (tmp_path / "large.json").rename(tmp_path / "private.json")
        (tmp_path / "large.json").symlink_to(tmp_path / "private.json")
    elif unsafe == "ancestor_symlink":
        (tmp_path / "private").mkdir()
        (tmp_path / "large.json").rename(tmp_path / "private/large.json")
        (tmp_path / "linked").symlink_to(tmp_path / "private", target_is_directory=True)
        record["path"] = record["evidence"][0]["path"] = "linked/large.json"
    if evidence_only:
        (tmp_path / "small.json").write_bytes(b"{}")
        record.update(path="small.json", digest=hashlib.sha256(b"{}").hexdigest())
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "artifacts": [record],
                "sourced_config": {
                    "declared": {"/agent": {"artifact_id": "selected", "pointer": "/agent"}},
                    "observed": {},
                },
            }
        )
    )
    wrapper = textwrap.dedent("""\
        import pathlib, runpy, sys
        original = pathlib.Path.read_bytes
        def guarded(path):
            assert path.name != "large.json", "unbounded selected source read"
            return original(path)
        pathlib.Path.read_bytes = guarded
        sys.argv = sys.argv[1:]
        sys.path.insert(0, str(pathlib.Path(sys.argv[0]).parent))
        runpy.run_path(sys.argv[0], run_name="__main__")
    """)
    cli = ROOT / "skills/eval-author-rubric/scripts/measure.py"
    result = subprocess.run(
        [sys.executable, "-c", wrapper, str(cli), "--root", str(tmp_path), "--selection", str(selection)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.stderr == ""
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert "PRIVATE_SENTINEL" not in result.stdout
    expected = "unknown" if unsafe == "oversized" else "fail"
    code = "identity.source_too_large" if unsafe == "oversized" else "identity.reference_invalid"
    assert report["measurements"]["identity"]["status"] == expected
    assert report["measurements"]["identity"]["diagnostics"] == [
        {"code": code, "subject": "selected", "status": expected}
    ]
