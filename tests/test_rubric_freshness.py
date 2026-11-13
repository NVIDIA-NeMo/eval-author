# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author/scripts"
sys.path.insert(0, str(SCRIPTS))


def checker():
    path = SCRIPTS / "rubric_freshness.py"
    assert path.is_file(), "Scoped freshness measurement is not implemented"
    spec = importlib.util.spec_from_file_location("rubric_freshness", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def artifact(root, ident, content, role="dependency", revision="r1"):
    from eval_author_shared.task_evidence import digest

    path = root / (ident + ".json")
    path.write_text(json.dumps(content))
    return {
        "id": ident,
        "role": role,
        "path": path.name,
        "digest": digest(path),
        "revision": revision,
        "access": "available",
        "selection_origin": "explicit",
        "source": "fixture",
        "evidence": [{"path": path.name, "digest": digest(path)}],
    }


def binding_source(root, result, dependencies):
    source = artifact(
        root,
        result["id"] + "-source",
        {
            "retained": {
                "evidence": {"id": result["id"], "digest": result["digest"]},
                "dependencies": [
                    {"id": row["id"], "digest": row["digest"], "revision": row["revision"], "impact": "material"}
                    for row in dependencies
                ],
            }
        },
        role="evidence",
    )
    selection = {"id": result["id"], "kind": "bindings", "source": source["id"], "pointer": "/retained"}
    return source, selection


@pytest.mark.parametrize("token", ["-1", "+0", "00", "01", " 0", "0 ", "٠", "-", "2"])
@pytest.mark.parametrize("seam", ["helper", "cli"])
def test_binding_pointer_rejects_noncanonical_or_missing_array_index(tmp_path, token, seam):
    dep = artifact(tmp_path, "dep", {})
    result = artifact(tmp_path, "result", {}, role="result")
    source, choice = binding_source(tmp_path, result, [dep])
    retained = json.loads((tmp_path / source["path"]).read_text())["retained"]
    source = artifact(tmp_path, source["id"], {"notes": [retained, retained]}, role="evidence")
    choice["pointer"] = "/notes/" + token
    report = freshness_report(tmp_path, [dep, result, source], [choice], seam)
    assert report["status"] == "fail"
    assert report["records"][0]["usable"] is False
    assert report["diagnostics"] == [{"code": "freshness.binding_invalid", "subject": "result", "status": "fail"}]


@pytest.mark.parametrize("token", ["0", "-1", "+0", "00", "01", " 0", "٠", "-", "", "a~1b", "a~0b", "~01"])
@pytest.mark.parametrize("index", ["0", "1"])
def test_binding_pointer_preserves_object_keys_and_canonical_array_index(tmp_path, token, index):
    dep = artifact(tmp_path, "dep", {})
    result = artifact(tmp_path, "result", {}, role="result")
    source, choice = binding_source(tmp_path, result, [dep])
    retained = json.loads((tmp_path / source["path"]).read_text())["retained"]
    key = token.replace("~1", "/").replace("~0", "~")
    source = artifact(tmp_path, source["id"], {"notes": [{key: retained}, {key: retained}]}, role="evidence")
    choice["pointer"] = "/notes/" + index + "/" + token
    report = checker().check_freshness(tmp_path, [dep, result, source], [choice])
    assert report["status"] == "pass"
    assert report["records"][0]["usable"] is True


@pytest.mark.parametrize("token", ["~", "~2", "x~00~3"])
def test_binding_pointer_rejects_malformed_escape_even_for_existing_key(tmp_path, token):
    dep = artifact(tmp_path, "dep", {})
    result = artifact(tmp_path, "result", {}, role="result")
    source, choice = binding_source(tmp_path, result, [dep])
    retained = json.loads((tmp_path / source["path"]).read_text())["retained"]
    source = artifact(tmp_path, source["id"], {token.replace("~0", "~"): retained}, role="evidence")
    choice["pointer"] = "/" + token
    report = checker().check_freshness(tmp_path, [dep, result, source], [choice])
    assert report["status"] == "fail"
    assert report["records"][0]["usable"] is False


def freshness_report(root, artifacts, choices, seam):
    if seam == "helper":
        return checker().check_freshness(root, artifacts, choices)
    import subprocess

    selection = root / "selection.json"
    selection.write_text(json.dumps({"artifacts": artifacts, "freshness": choices}))
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "skills/eval-author-rubric/scripts/measure.py"),
            "--root",
            str(root),
            "--selection",
            str(selection),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 1
    assert completed.stderr == ""
    assert "PRIVATE-SENTINEL" not in completed.stdout
    return json.loads(completed.stdout)["measurements"]["freshness"]


def test_freshness_hashes_and_parses_only_bounded_snapshots(tmp_path, monkeypatch):
    dep = artifact(tmp_path, "dep", {})
    result = artifact(tmp_path, "result", {}, role="result")
    source, choice = binding_source(tmp_path, result, [dep])

    def forbidden(*args, **kwargs):
        pytest.fail("Unbounded path read reached during freshness validation")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    report = checker().check_freshness(tmp_path, [dep, result, source], [choice])
    assert report["status"] == "pass"


def test_native_result_over_source_limit_is_rejected(native_panel):
    module = checker()
    result = native_panel.runs / "reference.json"
    payload = json.loads(result.read_text())
    payload["padding"] = "x" * module.MAX_SOURCE_BYTES
    native_panel.write(result, payload)
    receipt = native_panel.records / "reference.json"
    payload = json.loads(receipt.read_text())
    payload["result_sha256"] = native_panel.evidence.digest(result)
    native_panel.write(receipt, payload)
    artifacts, choice = native_selection(native_panel)
    report = module.check_freshness(native_panel.tmp_path, artifacts, [choice])
    assert report["status"] == "fail"
    assert report["records"][0]["usable"] is False


@pytest.mark.parametrize("placement", ["file", "parent", "root_ancestor"])
@pytest.mark.parametrize("seam", ["freshness", "descriptor_reader"])
def test_source_swap_after_path_check_never_reads_outside_root(tmp_path, monkeypatch, placement, seam):
    import os

    import rubric_identity

    module = checker()
    root = tmp_path / "parent" / "root"
    root.mkdir(parents=True)
    source = artifact(root, "source", {"safe": True})
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source.json").write_text('{"private":"PRIVATE-SENTINEL"}')
    (outside / "root").mkdir()
    (outside / "root/source.json").write_text('{"private":"PRIVATE-SENTINEL"}')
    original = module.local_file
    swapped = False

    def swap_after_check(selected_root, value):
        nonlocal swapped
        path = original(selected_root, value)
        if not swapped:
            swapped = True
            target = path if placement == "file" else root if placement == "parent" else root.parent
            target.rename(target.with_name(target.name + "-retained"))
            destination = outside / "source.json" if placement == "file" else outside
            target.symlink_to(destination, target_is_directory=placement != "file")
        return path

    # Observe real content reads, not only whether hash mismatch eventually fails.
    opened = []
    original_open = Path.open

    def track_open(path, *args, **kwargs):
        opened.append(os.fspath(path.resolve()))
        return original_open(path, *args, **kwargs)

    outside_inodes = {
        (p.stat().st_dev, p.stat().st_ino) for p in (outside / "source.json", outside / "root/source.json")
    }
    original_fdopen = os.fdopen

    def track_fdopen(fd, *args, **kwargs):
        info = os.fstat(fd)
        assert (info.st_dev, info.st_ino) not in outside_inodes
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(module if seam == "freshness" else rubric_identity, "local_file", swap_after_check)
    monkeypatch.setattr(Path, "open", track_open)
    monkeypatch.setattr(os, "fdopen", track_fdopen)
    with pytest.raises(ValueError):
        module._load(root, source)
    assert not any(path.startswith(str(outside)) for path in opened)


def test_native_validation_reuses_exact_selected_snapshots(native_panel, monkeypatch):
    from collections import Counter

    module = checker()
    artifacts, choice = native_selection(native_panel)
    original_read = module._read_file
    reads = []

    def capture(root, relative):
        reads.append(relative)
        data, reason = original_read(root, relative)
        # The retained native result must still be parsed from these sealed bytes.
        if relative == artifacts[-1]["path"]:
            (root / relative).write_text('{"private":"PRIVATE-SENTINEL"}')
        return data, reason

    def forbidden(*args, **kwargs):
        pytest.fail("Native validation must not reopen paths or discover files")

    monkeypatch.setattr(module, "_read_file", capture)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "rglob", forbidden)
    report = module.check_freshness(native_panel.tmp_path, artifacts, [choice])
    assert report["status"] == "unknown"
    assert report["records"][0]["native_classification"] == "passed"
    assert Counter(reads) == Counter({record["path"]: 1 for record in artifacts})
    assert "PRIVATE-SENTINEL" not in json.dumps(report)


@pytest.mark.parametrize("access", ["denied", "missing", "unresolved"])
def test_declared_unavailable_dependency_is_not_probed(tmp_path, monkeypatch, access):
    module = checker()
    dep = artifact(tmp_path, "dep", {})
    result = artifact(tmp_path, "result", {}, role="result")
    source, choice = binding_source(tmp_path, result, [dep])
    dep["access"] = access
    original_read = module._read_file

    def read(root, relative):
        assert relative != dep["path"]
        return original_read(root, relative)

    monkeypatch.setattr(module, "_read_file", read)
    report = module.check_freshness(tmp_path, [dep, result, source], [choice])
    assert report["status"] == "unknown"
    assert report["records"][0]["lineage"][0]["new"]["digest"] is None


def test_native_unknown_preserves_current_binding_neighbor(native_panel):
    root = native_panel.tmp_path
    artifacts, choice = native_selection(native_panel)
    dep = artifact(root, "dep", {})
    result = artifact(root, "result", {}, role="result")
    source, binding = binding_source(root, result, [dep])
    report = checker().check_freshness(root, artifacts + [dep, result, source], [choice, binding])
    assert report["status"] == "unknown"
    assert [(row["id"], row["status"], row["usable"]) for row in report["records"]] == [
        ("receipt", "unresolved", False),
        ("result", "current", True),
    ]
    assert report["diagnostics"] == [{"code": "freshness.unresolved", "subject": "receipt", "status": "unknown"}]


def test_snapshot_budget_counts_explicit_evidence_references(tmp_path, monkeypatch):
    module = checker()
    dep = artifact(tmp_path, "dep", {})
    result = artifact(tmp_path, "result", {}, role="result")
    source, choice = binding_source(tmp_path, result, [dep])
    for index in range(module.MAX_FILES):
        ref = artifact(tmp_path, f"ref-{index}", {})
        source["evidence"].append({"path": ref["path"], "digest": ref["digest"]})
    original_read = module._read_file
    reads = []

    def capture(root, relative):
        reads.append(relative)
        return original_read(root, relative)

    monkeypatch.setattr(module, "_read_file", capture)
    report = module.check_freshness(tmp_path, [dep, result, source], [choice])
    assert report["status"] == "fail"
    assert report["records"][0]["usable"] is False
    assert len(reads) <= module.MAX_FILES


@pytest.mark.parametrize("special", ["fifo", "directory"])
def test_selected_nonregular_source_fails_without_blocking(tmp_path, special):
    import os

    module = checker()
    dep = artifact(tmp_path, "dep", {})
    result = artifact(tmp_path, "result", {}, role="result")
    source, choice = binding_source(tmp_path, result, [dep])
    path = tmp_path / source["path"]
    path.unlink()
    if special == "fifo":
        os.mkfifo(path)
    else:
        path.mkdir()
    report = module.check_freshness(tmp_path, [dep, result, source], [choice])
    assert report["status"] == "fail"
    assert report["records"][0]["usable"] is False


def test_read_growth_is_bounded_and_rejected(tmp_path, monkeypatch):
    import os

    module = checker()
    dep = artifact(tmp_path, "dep", {})
    result = artifact(tmp_path, "result", {}, role="result")
    source, choice = binding_source(tmp_path, result, [dep])
    target = tmp_path / source["path"]
    inode = target.stat().st_ino
    original_fdopen = os.fdopen
    limits = []

    class GrowingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self, limit):
            limits.append(limit)
            with target.open("ab") as output:
                output.write(b"x" * module.MAX_SOURCE_BYTES)
            return self.stream.read(limit)

    def grow(fd, *args, **kwargs):
        is_target = os.fstat(fd).st_ino == inode
        stream = original_fdopen(fd, *args, **kwargs)
        return GrowingStream(stream) if is_target else stream

    monkeypatch.setattr(os, "fdopen", grow)
    report = module.check_freshness(tmp_path, [dep, result, source], [choice])
    assert limits == [module.MAX_SOURCE_BYTES + 1]
    assert report["status"] == "fail"
    assert report["records"][0]["usable"] is False


def test_current_source_bound_evidence_is_read_only(tmp_path):
    task = artifact(tmp_path, "task", {"instruction": "compute"}, role="task")
    result = artifact(tmp_path, "result", {"reward": 1}, role="result")
    source, selection = binding_source(tmp_path, result, [task])
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    report = checker().check_freshness(tmp_path, [task, result, source], [selection])
    assert report["status"] == "pass"
    assert report["records"][0]["status"] == "current"
    assert report["records"][0]["usable"] is True
    assert report["records"][0]["changed_dependencies"] == []
    assert report["records"][0]["lineage"] == [
        {
            "id": "task",
            "old": {"digest": task["digest"], "revision": "r1"},
            "new": {"digest": task["digest"], "revision": "r1"},
            "impact": "material",
        }
    ]
    assert report["criterion_3"] == "not_assessed"
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize("role", ["task", "scorer", "dependency", "ethos"])
def test_real_mutation_invalidates_only_bound_evidence(tmp_path, role):
    target = artifact(tmp_path, "target", {"value": 1}, role=role)
    neighbor = artifact(tmp_path, "neighbor", {"value": 1})
    old_result = artifact(tmp_path, "old-result", {"reward": 1}, role="result")
    other_result = artifact(tmp_path, "other-result", {"reward": 1}, role="result")
    source, selection = binding_source(tmp_path, old_result, [target])
    other_source, other_selection = binding_source(tmp_path, other_result, [neighbor])
    old_bytes = (tmp_path / source["path"]).read_bytes()
    old_digest = target["digest"]
    target = artifact(tmp_path, "target", {"value": 2}, role=role, revision="r2")
    # ETHOS impact is never inferred from a byte change, even with material bindings.
    report = checker().check_freshness(
        tmp_path, [target, neighbor, old_result, other_result, source, other_source], [selection, other_selection]
    )
    rows = {row["id"]: row for row in report["records"]}
    changed = rows["old-result"]
    assert changed["status"] == ("unresolved" if role == "ethos" else "stale")
    assert changed["usable"] is False
    assert changed["changed_dependencies"] == ["target"]
    assert changed["lineage"][0]["old"]["digest"] == old_digest
    assert changed["lineage"][0]["new"]["digest"] == target["digest"]
    assert rows["other-result"]["status"] == "current"
    assert (tmp_path / source["path"]).read_bytes() == old_bytes
    assert report["criterion_3"] == "not_assessed"
    assert report["status"] != "pass"


@pytest.mark.parametrize(
    "defect",
    [
        "wrong_result",
        "missing",
        "duplicate",
        "unselected",
        "source_changed",
        "source_duplicate",
        "selection_duplicate",
        "bad_digest",
        "bad_path",
        "private_json",
    ],
)
def test_invalid_bindings_fail_closed_without_private_payload(tmp_path, defect):
    dep = artifact(tmp_path, "dep", {})
    result = artifact(tmp_path, "result", {}, role="result")
    source, selection = binding_source(tmp_path, result, [dep])
    payload = json.loads((tmp_path / source["path"]).read_text())
    rows = [dep, result]
    choices = [selection]
    if defect == "wrong_result":
        payload["retained"]["evidence"]["id"] = "wrong"
    elif defect == "missing":
        payload["retained"]["dependencies"] = []
    elif defect == "duplicate":
        payload["retained"]["dependencies"] *= 2
    elif defect == "unselected":
        rows = [result]
    elif defect == "bad_digest":
        payload["retained"]["dependencies"][0]["digest"] = "not-a-digest"
    source = artifact(tmp_path, source["id"], payload, role="evidence")
    if defect == "source_changed":
        (tmp_path / source["path"]).write_text('{"private":"PRIVATE-SENTINEL"}')
    elif defect == "private_json":
        (tmp_path / source["path"]).write_text('{"PRIVATE-SENTINEL":1,"PRIVATE-SENTINEL":2}')
        from eval_author_shared.task_evidence import digest

        source["digest"] = digest(tmp_path / source["path"])
        source["evidence"][0]["digest"] = source["digest"]
    elif defect == "source_duplicate":
        rows.append(source)
    elif defect == "selection_duplicate":
        choices *= 2
    elif defect == "bad_path":
        source["path"] = "../PRIVATE-SENTINEL"
    report = checker().check_freshness(tmp_path, rows + [source], choices)
    assert report["status"] == "fail"
    assert all(not row["usable"] for row in report["records"])
    assert "PRIVATE-SENTINEL" not in json.dumps(report)


def test_prior_revision_keeps_old_result_and_new_result_separate(tmp_path):
    dep = artifact(tmp_path, "dep", {"v": 1})
    old = artifact(tmp_path, "old", {"reward": 0}, role="result")
    source, selection = binding_source(tmp_path, old, [dep])
    dep = artifact(tmp_path, "dep", {"v": 2}, revision="r2")
    new = artifact(tmp_path, "new", {"reward": 1}, role="result")
    new_source, new_selection = binding_source(tmp_path, new, [dep])
    report = checker().check_freshness(tmp_path, [dep, old, new, source, new_source], [selection, new_selection])
    assert [(r["id"], r["status"]) for r in report["records"]] == [("old", "stale"), ("new", "current")]


def test_missing_dependency_is_unresolved_with_retained_lineage(tmp_path):
    dep = artifact(tmp_path, "dep", {})
    result = artifact(tmp_path, "result", {}, role="result")
    source, selection = binding_source(tmp_path, result, [dep])
    (tmp_path / dep["path"]).unlink()
    report = checker().check_freshness(tmp_path, [dep, result, source], [selection])
    row = report["records"][0]
    assert row["status"] == "unresolved"
    assert row["lineage"][0]["old"]["digest"] == dep["digest"]
    assert row["lineage"][0]["new"]["digest"] is None
    assert report["status"] == "unknown"


@pytest.fixture
def native_panel(tmp_path, monkeypatch):
    # Reuse the existing synthetic Harbor receipt fixture, not a new native format.
    from test_audit_verifier_evidence import panel

    return getattr(panel, "__wrapped__")(tmp_path, monkeypatch)


def selected_file(root, ident, path, role="evidence"):
    from eval_author_shared.task_evidence import digest

    relative = path.relative_to(root).as_posix()
    return {
        "id": ident,
        "role": role,
        "path": relative,
        "digest": digest(path),
        "revision": None,
        "access": "available",
        "selection_origin": "explicit",
        "source": "synthetic native fixture",
        "evidence": [{"path": relative, "digest": digest(path)}],
    }


def native_selection(panel):
    receipt = panel.records / "reference.json"
    result = panel.runs / "reference.json"
    artifacts = [
        selected_file(panel.tmp_path, "receipt", receipt),
        selected_file(panel.tmp_path, "manifest", panel.manifest),
        selected_file(panel.tmp_path, "native-result", result, "result"),
    ]
    choice = {"id": "receipt", "kind": "native_control", "task_dir": panel.task.relative_to(panel.tmp_path).as_posix()}
    return artifacts, choice


@pytest.mark.parametrize("target", ["receipt", "manifest", "native-result"])
@pytest.mark.parametrize("direct", [False, True])
def test_cli_native_freshness_keeps_unavailable_alias_unknown(native_panel, target, direct):
    from test_rubric_identity import audited_rubric_cli

    artifacts, choice = native_selection(native_panel)
    alias = next(row for row in artifacts if row["id"] == target)
    private = native_panel.tmp_path / "private.json"
    private.write_bytes((native_panel.tmp_path / alias["path"]).read_bytes())
    denied_path = alias["path"] if direct else private.name
    artifacts.append(dict(alias, id="denied", path=denied_path, access="denied"))
    if direct:
        for record in artifacts[:3]:
            if record is not alias and Path(record["path"]).name == Path(denied_path).name:
                artifacts.append(dict(record, id="denied-" + record["id"], access="denied"))
    if not direct:
        alias["evidence"] = [{"path": private.name, "digest": alias["digest"]}]
    selection = native_panel.tmp_path / "selection.json"
    selection.write_text(json.dumps({"artifacts": artifacts, "freshness": [choice]}))
    completed = audited_rubric_cli(native_panel.tmp_path, selection, Path(denied_path).name)
    assert completed.stderr == ""
    assert completed.returncode == 1
    report = json.loads(completed.stdout)["measurements"]["freshness"]
    assert report["status"] == "unknown"
    assert report["diagnostics"] == [{"code": "freshness.unresolved", "subject": "receipt", "status": "unknown"}]
    assert report["records"][0]["usable"] is False


def test_native_without_task_file_closure_never_discovers(native_panel, monkeypatch):
    artifacts, choice = native_selection(native_panel)
    unselected = native_panel.task / "unselected-private.txt"
    unselected.write_text("PRIVATE-SENTINEL")

    def forbidden(*args, **kwargs):
        pytest.fail("Freshness must not discover or hash unselected task files")

    monkeypatch.setattr(Path, "rglob", forbidden)
    report = checker().check_freshness(native_panel.tmp_path, artifacts, [choice])
    row = report["records"][0]
    assert report["status"] == "unknown"
    assert row["status"] == "unresolved"
    assert row["usable"] is False
    assert row["native_classification"] == "passed"
    assert row["lineage"][0]["new"]["digest"] is None
    assert row["changed_dependencies"] == []
    assert "PRIVATE-SENTINEL" not in json.dumps(report)


def test_diagnostic_native_receipt_cannot_supply_control_freshness(native_panel):
    manifest = json.loads(native_panel.manifest.read_text())
    manifest["contract"]["cases"]["diagnostic"] = dict(manifest["contract"]["cases"]["reference"], kind="diagnostic")
    native_panel.write(native_panel.manifest, manifest)
    receipt = native_panel.records / "reference.json"
    retained = json.loads(receipt.read_text())
    retained["case"] = "diagnostic"
    retained["manifest_sha256"] = native_panel.evidence.digest(native_panel.manifest)
    native_panel.write(receipt, retained)
    artifacts, choice = native_selection(native_panel)
    report = checker().check_freshness(native_panel.tmp_path, artifacts, [choice])
    assert report["status"] == "fail"
    assert report["diagnostics"] == [{"code": "freshness.binding_invalid", "subject": "receipt", "status": "fail"}]
    assert "native_classification" not in report["records"][0]


def test_selected_native_control_uses_existing_read_only_validator(native_panel, monkeypatch):
    import subprocess

    def forbidden(*args, **kwargs):
        pytest.fail("Freshness must not execute native commands")

    monkeypatch.setattr(subprocess, "run", forbidden)
    artifacts, choice = native_selection(native_panel)
    before = {p: p.read_bytes() for p in native_panel.tmp_path.rglob("*") if p.is_file()}
    report = checker().check_freshness(native_panel.tmp_path, artifacts, [choice])
    row = report["records"][0]
    assert report["status"] == "unknown"
    assert row["status"] == "unresolved"
    assert row["usable"] is False
    assert row["native_classification"] == "passed"
    assert row["lineage"][0]["old"]["digest"] == native_panel.evidence.task_digest(native_panel.task)
    assert before == {p: p.read_bytes() for p in native_panel.tmp_path.rglob("*") if p.is_file()}


def test_native_unselected_malformed_neighbor_does_not_poison_selection(native_panel):
    artifacts, choice = native_selection(native_panel)
    artifacts.append({"id": "unused", "path": None})
    report = checker().check_freshness(native_panel.tmp_path, artifacts, [choice])
    assert report["status"] == "unknown"
    assert report["records"][0]["native_classification"] == "passed"


@pytest.mark.parametrize("seam", ["helper", "cli"])
@pytest.mark.parametrize("field", ["health", "checks"])
@pytest.mark.parametrize(
    "bounds",
    [
        {"min": 10**400, "max": 10**401},
        {"min": 0, "max": 10**400},
        {"min": -(10**400), "max": 1},
        {"min": 2, "max": 1},
        {"min": False, "max": 1},
        {"min": "PRIVATE-SENTINEL", "max": 1},
    ],
    ids=["overflow_both", "overflow_max", "overflow_min", "reversed", "boolean", "private_string"],
)
def test_malformed_native_contract_bounds_fail_with_fixed_diagnostic(native_panel, seam, field, bounds):
    manifest = json.loads(native_panel.manifest.read_text())
    manifest["contract"]["cases"]["reference"][field] = [{"pointer": "/verifier_result/rewards/reward", **bounds}]
    manifest["private"] = "PRIVATE-SENTINEL"
    native_panel.write(native_panel.manifest, manifest)
    receipt = native_panel.records / "reference.json"
    retained = json.loads(receipt.read_text())
    retained["manifest_sha256"] = native_panel.evidence.digest(native_panel.manifest)
    native_panel.write(receipt, retained)
    artifacts, choice = native_selection(native_panel)
    report = freshness_report(native_panel.tmp_path, artifacts, [choice], seam)
    assert report["status"] == "fail"
    assert report["records"] == [
        {"id": "receipt", "status": "unresolved", "usable": False, "changed_dependencies": [], "lineage": []}
    ]
    assert report["diagnostics"] == [{"code": "freshness.binding_invalid", "subject": "receipt", "status": "fail"}]
    assert report["criterion_3"] == "not_assessed"
    assert "PRIVATE-SENTINEL" not in json.dumps(report)


@pytest.mark.parametrize("defect", ["contract_list", "native_list", "agent_list"])
def test_native_malformed_selected_objects_fail_closed(native_panel, defect):
    receipt, result = native_panel.record("reference", 1)
    if defect == "contract_list":
        data = json.loads(native_panel.manifest.read_text())
        data["contract"] = []
        native_panel.write(native_panel.manifest, data)
        data = json.loads(receipt.read_text())
        data["manifest_sha256"] = native_panel.evidence.digest(native_panel.manifest)
    elif defect == "agent_list":
        native_data = json.loads(result.read_text())
        native_data["config"] = {"agent": []}
        native_panel.write(result, native_data)
        data = json.loads(receipt.read_text())
        data["case"] = "early-exit"
        data["result_sha256"] = native_panel.evidence.digest(result)
    else:
        native_panel.write(result, [])
        data = json.loads(receipt.read_text())
        data["result_sha256"] = native_panel.evidence.digest(result)
    native_panel.write(receipt, data)
    artifacts, choice = native_selection(native_panel)
    report = checker().check_freshness(native_panel.tmp_path, artifacts, [choice])
    assert report["status"] == "fail"
    assert report["records"][0]["usable"] is False


@pytest.mark.parametrize("mutation", ["task", "scorer", "dependency", "mode"])
def test_unselected_native_tree_mutation_remains_unknown(native_panel, mutation):
    artifacts, choice = native_selection(native_panel)
    retained = {p: p.read_bytes() for p in [native_panel.manifest, native_panel.records / "reference.json"]}
    if mutation == "mode":
        (native_panel.task / "verify.py").chmod(0o700)
    else:
        (native_panel.task / (mutation + ".txt")).write_text("changed")
    report = checker().check_freshness(native_panel.tmp_path, artifacts, [choice])
    row = report["records"][0]
    assert row["status"] == "unresolved"
    assert row["usable"] is False
    assert row["native_classification"] == "passed"
    assert row["changed_dependencies"] == []
    assert row["lineage"][0]["new"]["digest"] is None
    assert retained == {p: p.read_bytes() for p in retained}


@pytest.mark.parametrize(
    "damage",
    [
        "unselected_result",
        "unselected_manifest",
        "wrong_hash",
        "wrong_identity",
        "health",
        "lying_status",
        "gym",
        "baseline",
        "wrong_schema",
        "unselected_task",
        "duplicate_path",
        "source_symlink",
    ],
)
def test_native_damage_cannot_become_usable(native_panel, damage):
    receipt = native_panel.records / "reference.json"
    result = native_panel.runs / "reference.json"
    data = json.loads(receipt.read_text())
    if damage == "wrong_hash":
        data["result_sha256"] = "0" * 64
    elif damage == "wrong_identity":
        data["native_run_id"] = "other"
    elif damage == "health":
        native_panel.record("reference", 1, error={"private": "PRIVATE-SENTINEL"})
        data = json.loads(receipt.read_text())
    elif damage == "lying_status":
        data["status"] = "behavior_failed"
    elif damage == "gym":
        manifest = json.loads(native_panel.manifest.read_text())
        manifest["contract"]["provider"] = "gym"
        native_panel.write(native_panel.manifest, manifest)
        data["manifest_sha256"] = native_panel.evidence.digest(native_panel.manifest)
    elif damage == "baseline":
        data["purpose"] = "baseline"
    elif damage == "wrong_schema":
        data["schema"] = "invented"
    native_panel.write(receipt, data)
    artifacts, choice = native_selection(native_panel)
    if damage == "unselected_result":
        artifacts = [a for a in artifacts if a["id"] != "native-result"]
    elif damage == "unselected_manifest":
        artifacts = [a for a in artifacts if a["id"] != "manifest"]
    elif damage == "unselected_task":
        other = native_panel.tmp_path / "other"
        other.mkdir()
        choice["task_dir"] = "other"
    elif damage == "duplicate_path":
        artifacts.append({**artifacts[-1], "id": "alias"})
    elif damage == "source_symlink":
        result.rename(result.with_suffix(".retained"))
        result.symlink_to(result.with_suffix(".retained"))
    (native_panel.task / "verify.py").write_text("changed")
    report = checker().check_freshness(native_panel.tmp_path, artifacts, [choice])
    assert report["status"] == "fail"
    assert report["records"][0]["usable"] is False
    assert "PRIVATE-SENTINEL" not in json.dumps(report)


def test_failed_native_control_retains_negative_assertions_with_unknown_freshness(native_panel):
    native_panel.record("reference", 0)
    artifacts, choice = native_selection(native_panel)
    report = checker().check_freshness(native_panel.tmp_path, artifacts, [choice])
    row = report["records"][0]
    assert row["status"] == "unresolved"
    assert report["status"] == "unknown"
    assert row["usable"] is False
    assert row["native_classification"] == "false_rejection"
    assert report["criterion_3"] == "not_assessed"


@pytest.mark.parametrize("role", ["dependency", "ethos"])
def test_unknown_impact_changed_binding_remains_unresolved(tmp_path, role):
    dep = artifact(tmp_path, "dep", {}, role=role)
    result = artifact(tmp_path, "result", {}, role="result")
    source, selection = binding_source(tmp_path, result, [dep])
    payload = json.loads((tmp_path / source["path"]).read_text())
    payload["retained"]["dependencies"][0]["impact"] = "unknown"
    source = artifact(tmp_path, source["id"], payload, role="evidence")
    dep = artifact(tmp_path, "dep", {"changed": True}, role=role)
    report = checker().check_freshness(tmp_path, [dep, result, source], [selection])
    assert report["status"] == "unknown"
    assert report["records"][0]["status"] == "unresolved"
    assert report["records"][0]["usable"] is False


@pytest.mark.parametrize("target", ["dependency", "source", "result"])
@pytest.mark.parametrize("access", ["missing", "denied", "unresolved"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("direct", [False, True])
def test_cli_freshness_never_opens_selected_unavailable_alias(tmp_path, target, access, reverse, direct):
    from test_rubric_identity import audited_rubric_cli

    dep = artifact(tmp_path, "dep", {})
    result = artifact(tmp_path, "result", {}, role="result")
    source, choice = binding_source(tmp_path, result, [dep])
    neighbor = artifact(tmp_path, "neighbor", {}, role="result")
    neighbor_dep = artifact(tmp_path, "neighbor-dep", {})
    neighbor_source, neighbor_choice = binding_source(tmp_path, neighbor, [neighbor_dep])
    alias = {"dependency": dep, "source": source, "result": result}[target]
    private = tmp_path / "private.json"
    private.write_bytes((tmp_path / alias["path"]).read_bytes())
    unavailable = dict(alias, id="unavailable", path=private.name, access=access)
    alias["evidence"] = [{"path": private.name, "digest": alias["digest"]}]
    if direct:
        alias["path"] = private.name
    artifacts = [unavailable, dep, result, source, neighbor, neighbor_dep, neighbor_source]
    if reverse:
        artifacts.reverse()
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"artifacts": artifacts, "freshness": [choice, neighbor_choice]}))
    completed = audited_rubric_cli(tmp_path, selection, "private.json")
    assert completed.stderr == ""
    assert completed.returncode == 1
    report = json.loads(completed.stdout)["measurements"]["freshness"]
    assert report["status"] == "unknown"
    assert report["diagnostics"] == [{"code": "freshness.unresolved", "subject": "result", "status": "unknown"}]
    rows = {row["id"]: row for row in report["records"]}
    assert rows["result"]["status"] == "unresolved"
    assert rows["result"]["usable"] is False
    assert rows["neighbor"]["status"] == "current"
    assert rows["neighbor"]["usable"] is True


def test_revision_only_change_is_stale_and_report_cannot_replace_source(tmp_path):
    dep = artifact(tmp_path, "dep", {})
    result = artifact(tmp_path, "result", {}, role="result")
    source, selection = binding_source(tmp_path, result, [dep])
    dep["revision"] = "r2"
    report = checker().check_freshness(tmp_path, [dep, result, source], [selection])
    assert report["records"][0]["status"] == "stale"
    report_source = artifact(tmp_path, source["id"], report, role="evidence")
    selection["pointer"] = "/records/0"
    replay = checker().check_freshness(tmp_path, [dep, result, report_source], [selection])
    assert replay["status"] == "fail"
    assert replay["records"][0]["usable"] is False
