# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
CLI = ROOT / "skills/eval-author-rubric/scripts/measure.py"


def retain(root, name, value, role="input"):
    data = value if isinstance(value, bytes) else json.dumps(value).encode()
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    return {
        "id": name,
        "role": role,
        "path": name,
        "digest": digest,
        "revision": None,
        "access": "available",
        "selection_origin": "explicit",
        "source": "selected",
        "evidence": [{"path": name, "digest": digest}],
    }


@pytest.fixture
def selected(tmp_path):
    artifact = retain(tmp_path, "private/ground-truth/initial.txt", b"EXACT_PRIVATE_BYTES\r\n")
    trace = retain(
        tmp_path,
        "private/safe.atif.json",
        {
            "schema_version": "ATIF-v1.7",
            "session_id": "private-session",
            "steps": [
                {"step_id": 7, "message": "EXACT_PRIVATE_BYTES\r\n"},
                {"step_id": 8, "message": "wrong bytes"},
            ],
        },
        "trace",
    )
    candidate = retain(
        tmp_path,
        "candidate.json",
        {
            "schema": "nemo.eval_author.trace_environment_candidate.v2",
            "state_basis": "recorded",
            "ground_truth": {
                "artifacts": [
                    {
                        "kind": "fixture",
                        "path": artifact["path"],
                        "sha256": "sha256:" + artifact["digest"],
                        "provenance": {
                            "kind": "atif_step",
                            "step_ids": [7],
                            "uri": None,
                            "revision": None,
                            "source_id": None,
                        },
                        "notes": "Selected initial state",
                    }
                ]
            },
        },
    )
    summary = retain(
        tmp_path,
        "summary.json",
        {
            "schema": "nemo.eval_author.trace_environment_summary.v3",
            "candidate": {"path": "candidate.json", **json.loads((tmp_path / candidate["path"]).read_bytes())},
            "source": {
                "safe_path": trace["path"],
                "safe_sha256": "sha256:" + trace["digest"],
                "safe_size_bytes": (tmp_path / trace["path"]).stat().st_size,
            },
        },
    )
    entry = {
        "artifact_id": artifact["id"],
        "candidate": {"artifact_id": candidate["id"], "pointer": "/ground_truth/artifacts/0"},
        "source": {"artifact_id": summary["id"], "pointer": "/source"},
        "trace_id": trace["id"],
        "step": {"pointer": "/steps/0", "step_id": 7},
        "content": {"pointer": "/message", "encoding": "utf-8"},
        "declaration": "recovered",
    }
    return {"artifacts": [artifact, trace, candidate, summary], "reconstruction": [entry]}


def run_cli(root, selection):
    path = root / "selection.json"
    path.write_text(json.dumps(selection))
    proc = subprocess.run(
        [sys.executable, str(CLI), "--root", str(root), "--selection", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.stderr == ""
    assert proc.returncode in (0, 1)
    return json.loads(proc.stdout)


def test_cli_measures_exact_selected_reconstruction_without_scoring(tmp_path, selected):
    baseline = run_cli(tmp_path, {"artifacts": selected["artifacts"]})
    report = run_cli(tmp_path, selected)
    assert "reconstruction" in report["measurements"], "CLI must consume the optional reconstruction selection"
    measurement = report["measurements"]["reconstruction"]
    assert measurement["status"] == "pass"
    row = measurement["records"][0]
    assert row["artifact_digest"] == selected["artifacts"][0]["digest"]
    assert row["source_content_digest"] == row["artifact_digest"]
    assert row["source_proof"] == "exact_selected_bytes"
    assert row["declaration"] == "recovered"
    assert row["semantic_proof"] is False
    assert row["source_step_id"] == 7
    assert row["source_step_index"] == 0
    assert report["items"] == baseline["items"]
    assert report["preflight_success"] is False
    assert "EXACT_PRIVATE_BYTES" not in json.dumps(measurement)
    assert "private-session" not in json.dumps(measurement)
    assert run_cli(tmp_path, selected) == report


def rewrite(root, selection, ident, edit):
    record = next(row for row in selection["artifacts"] if row["id"] == ident)
    value = json.loads((root / record["path"]).read_bytes())
    edit(value)
    updated = retain(root, record["path"], value, record["role"])
    record.update(updated)


def bind_summary_candidate(root, selection):
    candidate = json.loads((root / "candidate.json").read_bytes())
    rewrite(root, selection, "summary.json", lambda doc: doc.update(candidate={"path": "candidate.json", **candidate}))


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("artifact_changed", "artifact_digest_mismatch"),
        ("candidate_changed", "candidate_digest_mismatch"),
        ("trace_changed", "trace_digest_mismatch"),
        ("source_changed", "source_digest_mismatch"),
        ("wrong_step", "step_mismatch"),
        ("missing_step", "step_missing"),
        ("wrong_content", "content_digest_mismatch"),
        ("dangling_candidate", "candidate_not_selected"),
        ("dangling_trace", "trace_not_selected"),
        ("candidate_path", "candidate_artifact_mismatch"),
        ("candidate_digest", "candidate_artifact_mismatch"),
        ("candidate_step", "candidate_step_mismatch"),
        ("source_path", "source_trace_mismatch"),
        ("source_digest", "source_trace_mismatch"),
        ("source_size", "source_trace_mismatch"),
    ],
)
def test_cli_rejects_wrong_bindings_with_specific_diagnostics(tmp_path, selected, mutation, code):
    entry = selected["reconstruction"][0]
    if mutation.endswith("_changed"):
        ident = {
            "artifact_changed": entry["artifact_id"],
            "candidate_changed": "candidate.json",
            "trace_changed": entry["trace_id"],
            "source_changed": "summary.json",
        }[mutation]
        (tmp_path / ident).write_bytes(b"changed-private-bytes")
    elif mutation == "wrong_step":
        entry["step"]["pointer"] = "/steps/1"
    elif mutation == "missing_step":
        entry["step"]["pointer"] = "/steps/99"
    elif mutation == "wrong_content":
        rewrite(
            tmp_path,
            selected,
            "candidate.json",
            lambda doc: doc["ground_truth"]["artifacts"][0]["provenance"].update(step_ids=[8]),
        )
        entry["step"] = {"pointer": "/steps/1", "step_id": 8}
        bind_summary_candidate(tmp_path, selected)
    elif mutation.startswith("dangling_"):
        if mutation == "dangling_trace":
            entry["trace_id"] = "not-selected"
        else:
            entry["candidate"]["artifact_id"] = "not-selected"
    elif mutation.startswith("candidate_"):

        def edit(doc):
            retained = doc["ground_truth"]["artifacts"][0]
            if mutation == "candidate_path":
                retained["path"] = "private/ground-truth/other.txt"
            elif mutation == "candidate_digest":
                retained["sha256"] = "sha256:" + "0" * 64
            else:
                retained["provenance"]["step_ids"] = [8]

        rewrite(tmp_path, selected, "candidate.json", edit)
    else:
        field, value = {
            "source_path": ("safe_path", "private/other.json"),
            "source_digest": ("safe_sha256", "sha256:" + "0" * 64),
            "source_size": ("safe_size_bytes", 1),
        }[mutation]
        rewrite(tmp_path, selected, "summary.json", lambda doc: doc["source"].update({field: value}))
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["status"] == "fail"
    assert measurement["diagnostics"] == [
        {"code": "reconstruction." + code, "subject": "reconstruction:0", "status": "fail"}
    ]
    assert measurement["records"][0]["source_proof"] == "unknown"


@pytest.mark.parametrize("declaration", ["partial", "generated"])
@pytest.mark.parametrize("content_available", [False, True])
def test_cli_retains_known_identity_without_recovery_claim(tmp_path, selected, declaration, content_available):
    entry = selected["reconstruction"][0]
    entry["declaration"] = declaration
    if not content_available:
        entry["content"] = None
    rewrite(tmp_path, selected, "candidate.json", lambda doc: doc.update(state_basis="reconstructed"))
    bind_summary_candidate(tmp_path, selected)
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["status"] == "unknown"
    assert measurement["diagnostics"] == [
        {
            "code": "reconstruction." + declaration + "_source_proof_unknown",
            "subject": "reconstruction:0",
            "status": "unknown",
        }
    ]
    row = measurement["records"][0]
    assert row["artifact_digest"] == selected["artifacts"][0]["digest"]
    assert row["source_proof"] == "unknown"
    assert row["declaration"] == declaration
    assert row["source_content_digest"] == (row["artifact_digest"] if content_available else None)


@pytest.mark.parametrize("access", ["missing", "denied", "unresolved"])
def test_cli_unavailable_trace_keeps_artifact_identity_and_valid_neighbor(tmp_path, selected, access):
    import copy

    neighbor = copy.deepcopy(selected["reconstruction"][0])
    trace = copy.deepcopy(selected["artifacts"][1])
    trace.update(id="unavailable-trace", path="DO_NOT_READ", access=access)
    selected["artifacts"].append(trace)
    selected["reconstruction"][0]["trace_id"] = trace["id"]
    selected["reconstruction"].append(neighbor)
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["status"] == "unknown"
    assert measurement["records"][0]["artifact_digest"] == selected["artifacts"][0]["digest"]
    assert measurement["records"][0]["source_proof"] == "unknown"
    assert measurement["records"][1]["status"] == "pass"
    assert measurement["diagnostics"] == [
        {"code": "reconstruction.trace_access_" + access, "subject": "reconstruction:0", "status": "unknown"}
    ]


def test_recovered_with_unrecorded_bytes_stays_unknown(tmp_path, selected):
    selected["reconstruction"][0]["content"] = None
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["status"] == "unknown"
    assert measurement["diagnostics"][0]["code"] == "reconstruction.content_unavailable"


@pytest.mark.parametrize("field", ["artifact_id", "candidate", "source", "trace_id", "step", "content", "declaration"])
@pytest.mark.parametrize("bad", [False, 1, [], {}, "", None])
def test_cli_malformed_entries_are_private_diagnostics(tmp_path, selected, field, bad):
    selected["reconstruction"][0][field] = bad
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["status"] != "pass"
    assert "EXACT_PRIVATE_BYTES" not in json.dumps(measurement)
    assert measurement["diagnostics"][0]["code"].startswith("reconstruction.")


@pytest.mark.parametrize(
    "pointer", ["/steps/00", "/steps/+0", "/steps/-1", "/steps/0/message", "/steps/0~2", "/steps/" + "9" * 1100]
)
def test_cli_rejects_noncanonical_step_pointers(tmp_path, selected, pointer):
    selected["reconstruction"][0]["step"]["pointer"] = pointer
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["diagnostics"][0]["code"] == "reconstruction.step_pointer_invalid"


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("duplicate", "step_ambiguous"),
        ("unknown_provenance", "candidate_step_missing"),
        ("state_basis", "candidate_state_invalid"),
        ("noncanonical_path", "candidate_path_invalid"),
    ],
)
def test_cli_rejects_ambiguous_source_projections(tmp_path, selected, mutation, code):
    if mutation == "duplicate":
        rewrite(
            tmp_path,
            selected,
            "private/safe.atif.json",
            lambda doc: doc["steps"].append({"step_id": 7, "message": "other"}),
        )
        trace = selected["artifacts"][1]
        rewrite(
            tmp_path,
            selected,
            "summary.json",
            lambda doc: doc["source"].update(
                safe_sha256="sha256:" + trace["digest"], safe_size_bytes=(tmp_path / trace["path"]).stat().st_size
            ),
        )
    else:

        def edit(doc):
            if mutation == "unknown_provenance":
                doc["ground_truth"]["artifacts"][0]["provenance"]["step_ids"].append(999)
            elif mutation == "state_basis":
                doc["state_basis"] = []
            else:
                doc["ground_truth"]["artifacts"][0]["path"] = "private//ground-truth/initial.txt"

        rewrite(tmp_path, selected, "candidate.json", edit)
        bind_summary_candidate(tmp_path, selected)
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["status"] == "fail"
    assert measurement["diagnostics"][0]["code"] == "reconstruction." + code


@pytest.mark.parametrize("kind", ["records", "bytes", "files", "references"])
def test_helper_bounds_work_before_extra_reads(tmp_path, selected, monkeypatch, kind):
    import rubric_reconstruction as checker

    original = checker._read_selected
    reads = []

    def read(*args):
        reads.append(args[1])
        return original(*args)

    monkeypatch.setattr(checker, "_read_selected", read)
    if kind == "records":
        monkeypatch.setattr(checker, "MAX_RECORDS", 1)
    elif kind == "bytes":
        monkeypatch.setattr(checker, "MAX_TOTAL_BYTES", 1)
    elif kind == "files":
        monkeypatch.setattr(checker, "MAX_FILES", 1)
    else:
        selected["artifacts"][0]["evidence"] *= 65
    result = checker.project_reconstruction(tmp_path, selected["artifacts"], selected["reconstruction"])
    assert result["status"] != "pass"
    assert len(reads) <= (0 if kind in ("records", "references") else 1)


@pytest.mark.parametrize("access", ["missing", "denied", "unresolved"])
def test_helper_does_not_read_unavailable_paths(tmp_path, selected, monkeypatch, access):
    import rubric_reconstruction as checker

    selected["artifacts"][1]["access"] = access
    original = checker._read_selected

    def read(root, path, limit):
        assert path != selected["artifacts"][1]["path"]
        return original(root, path, limit)

    monkeypatch.setattr(checker, "_read_selected", read)
    result = checker.project_reconstruction(tmp_path, selected["artifacts"], selected["reconstruction"])
    assert result["status"] == "unknown"


@pytest.mark.parametrize("kind", ["leaf", "ancestor", "fifo"])
def test_cli_rejects_nofollow_and_nonregular_sources(tmp_path, selected, kind):
    import os

    path = tmp_path / "private/safe.atif.json"
    if kind == "ancestor":
        (tmp_path / "private").rename(tmp_path / "moved")
        (tmp_path / "private").symlink_to(tmp_path / "moved", target_is_directory=True)
    else:
        path.rename(tmp_path / "retained")
        if kind == "leaf":
            path.symlink_to(tmp_path / "retained")
        else:
            os.mkfifo(path)
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["records"][0]["source_proof"] == "unknown"
    assert measurement["status"] == "fail"


def test_helper_denied_reference_is_never_read_via_available_artifact(tmp_path, selected, monkeypatch):
    import rubric_reconstruction as checker

    trace = selected["artifacts"][1]
    trace["access"] = "denied"
    selected["artifacts"][0]["evidence"].append({"path": trace["path"], "digest": trace["digest"]})
    original = checker._read_selected

    def read(root, path, limit):
        assert path != trace["path"]
        return original(root, path, limit)

    monkeypatch.setattr(checker, "_read_selected", read)
    result = checker.project_reconstruction(tmp_path, selected["artifacts"], selected["reconstruction"])
    assert result["status"] == "unknown"


@pytest.mark.parametrize("schema", [None, "unrelated.v3", "nemo.eval_author.trace_environment_summary.v1", [], True])
def test_cli_rejects_unsupported_source_summary_schema(tmp_path, selected, schema):
    rewrite(tmp_path, selected, "summary.json", lambda doc: doc.update(schema=schema))
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["status"] == "fail"
    assert measurement["diagnostics"][0]["code"] == "reconstruction.source_schema_unsupported"
    assert measurement["records"][0]["source_proof"] == "unknown"


@pytest.mark.parametrize("mutation", ["missing", "null", "path", "content", "extra", "number_type"])
def test_cli_rejects_summary_candidate_contradictions(tmp_path, selected, mutation):
    def edit(doc):
        if mutation == "missing":
            del doc["candidate"]
        elif mutation == "null":
            doc["candidate"] = None
        elif mutation == "path":
            doc["candidate"]["path"] = "other.json"
        elif mutation == "content":
            doc["candidate"]["ground_truth"]["artifacts"][0]["notes"] = "Foreign candidate"
        elif mutation == "extra":
            doc["candidate"]["extra"] = "Foreign field"
        else:
            doc["candidate"]["ground_truth"]["artifacts"][0]["provenance"]["step_ids"] = [7.0]

    rewrite(tmp_path, selected, "summary.json", edit)
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["status"] == "fail"
    assert measurement["diagnostics"][0]["code"] == "reconstruction.candidate_source_mismatch"
    assert measurement["records"][0]["source_proof"] == "unknown"


def test_cli_rejects_foreign_same_directory_summary_with_equal_step_bytes(tmp_path, selected):
    import copy

    neighbor = copy.deepcopy(selected["reconstruction"][0])
    trace_doc = json.loads((tmp_path / selected["artifacts"][1]["path"]).read_bytes())
    trace_doc["session_id"] = "foreign-session"
    trace = retain(tmp_path, "private/foreign.atif.json", trace_doc, "trace")
    summary = json.loads((tmp_path / "summary.json").read_bytes())
    summary["source"].update(
        safe_path=trace["path"],
        safe_sha256="sha256:" + trace["digest"],
        safe_size_bytes=(tmp_path / trace["path"]).stat().st_size,
    )
    selected["artifacts"].extend([trace, retain(tmp_path, "foreign-summary.json", summary)])
    entry = selected["reconstruction"][0]
    entry["source"]["artifact_id"] = "foreign-summary.json"
    entry["trace_id"] = trace["id"]
    selected["reconstruction"].append(neighbor)
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["status"] == "fail"
    assert measurement["diagnostics"] == [
        {"code": "reconstruction.candidate_source_mismatch", "subject": "reconstruction:0", "status": "fail"}
    ]
    assert measurement["records"][0]["source_proof"] == "unknown"
    assert measurement["records"][1]["status"] == "pass"


@pytest.mark.parametrize("embedded_path", [False, True])
def test_cli_rejects_candidate_path_substitution(tmp_path, selected, embedded_path):
    candidate = json.loads((tmp_path / "candidate.json").read_bytes())
    if embedded_path:
        candidate["path"] = "foreign-candidate.json"
    selected["artifacts"].append(retain(tmp_path, "foreign-candidate.json", candidate))
    selected["reconstruction"][0]["candidate"]["artifact_id"] = "foreign-candidate.json"
    rewrite(
        tmp_path, selected, "summary.json", lambda doc: doc.update(candidate={"path": "candidate.json", **candidate})
    )
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["status"] == "fail"
    assert measurement["diagnostics"][0]["code"] == "reconstruction.candidate_source_mismatch"


@pytest.mark.parametrize("summary_path", ["candidate.json", "foreign-candidate.json"])
def test_cli_rejects_candidate_document_overriding_owner_path(tmp_path, selected, summary_path):
    rewrite(tmp_path, selected, "candidate.json", lambda doc: doc.update(path="foreign-candidate.json"))
    bind_summary_candidate(tmp_path, selected)
    rewrite(tmp_path, selected, "summary.json", lambda doc: doc["candidate"].update(path=summary_path))
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["status"] == "fail"
    assert measurement["diagnostics"][0]["code"] == "reconstruction.candidate_source_mismatch"


def test_cli_rejects_source_from_another_workspace(tmp_path, selected):
    source = json.loads((tmp_path / "summary.json").read_bytes())
    selected["artifacts"].append(retain(tmp_path, "other/summary.json", source))
    selected["reconstruction"][0]["source"]["artifact_id"] = "other/summary.json"
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["diagnostics"][0]["code"] == "reconstruction.candidate_source_mismatch"


def test_cli_rejects_unowned_source_pointer(tmp_path, selected):
    rewrite(tmp_path, selected, "summary.json", lambda doc: doc.update(other=doc["source"]))
    selected["reconstruction"][0]["source"]["pointer"] = "/other"
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["diagnostics"][0]["code"] == "reconstruction.source_pointer_invalid"


@pytest.mark.parametrize("source", ["candidate.json", "summary.json", "private/safe.atif.json"])
@pytest.mark.parametrize("bad", [None, [], True, 1, "PRIVATE_INVALID"])
def test_cli_malformed_retained_sources_cannot_crash(tmp_path, selected, source, bad):
    record = next(row for row in selected["artifacts"] if row["id"] == source)
    record.update(retain(tmp_path, source, bad))
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["status"] == "fail"
    assert "PRIVATE_INVALID" not in json.dumps(measurement)


@pytest.fixture
def current_selected(tmp_path):
    from test_trace_environment import _atif, _candidate, _run, _write_json

    workspace_root = tmp_path / ".eval-author/.working/trace-environments"
    code, initialized = _run("init", "--root", str(workspace_root), "--task-id", "recovered-fixture")
    assert code == 0, initialized
    root = Path(initialized["task_dir"])
    trace_doc = _atif()
    trace_doc["steps"][0]["message"] = "EXACT_PRIVATE_BYTES\r\n"
    source = tmp_path / "source.json"
    _write_json(source, trace_doc)
    code, prepared = _run("prepare", "--task-dir", str(root), "--source", str(source), "--source-kind", "atif")
    assert code == 0, prepared
    artifact = retain(root, "private/ground-truth/initial.txt", b"EXACT_PRIVATE_BYTES\r\n")
    (root / artifact["path"]).chmod(0o600)
    _candidate(
        root,
        status="no_candidate",
        ground_truth={
            "availability": "available",
            "use": "comparison_only",
            "absence_reason": None,
            "artifacts": [
                {
                    "kind": "fixture",
                    "path": artifact["path"],
                    "sha256": "sha256:" + artifact["digest"],
                    "provenance": {
                        "kind": "trace_turn",
                        "turn_ids": ["trajectory/turn/1"],
                        "uri": None,
                        "revision": None,
                        "source_id": None,
                    },
                    "notes": "Selected initial state",
                }
            ],
        },
    )
    code, finalized = _run("finalize", "--task-dir", str(root), "--status", "no_candidate")
    assert code == 0, finalized
    records = [artifact]
    for name in ("candidate.json", "summary.json", "safe/trace.json"):
        records.append(retain(root, name, (root / name).read_bytes()))
    selection = {
        "artifacts": records,
        "reconstruction": [
            {
                "artifact_id": artifact["id"],
                "candidate": {"artifact_id": "candidate.json", "pointer": "/ground_truth/artifacts/0"},
                "source": {"artifact_id": "summary.json", "pointer": "/source"},
                "trace_id": "safe/trace.json",
                "step": {"pointer": "/root_spans/0/turns/0", "step_id": "trajectory/turn/1"},
                "content": {"pointer": "/content", "encoding": "utf-8"},
                "declaration": "recovered",
            }
        ],
    }
    return root, selection


def test_cli_accepts_actual_preparation_and_finalization_producer(current_selected):
    root, selection = current_selected
    summary = json.loads((root / "summary.json").read_bytes())
    assert summary["schema"] == "nemo.eval_author.trace_environment_summary.v4"
    assert summary["candidate"]["schema"] == "nemo.eval_author.trace_environment_candidate.v3"
    assert summary["source"]["safe_path"] == "safe/trace.json"
    measurement = run_cli(root, selection)["measurements"]["reconstruction"]
    assert measurement["status"] == "pass", measurement
    assert measurement["records"][0]["source_proof"] == "exact_selected_bytes"
    assert measurement["records"][0]["semantic_proof"] is False


@pytest.mark.parametrize("version", [2, 3])
def test_historical_atif_summary_remains_read_only_supported(tmp_path, selected, version):
    rewrite(
        tmp_path,
        selected,
        "summary.json",
        lambda doc: doc.update(schema=f"nemo.eval_author.trace_environment_summary.v{version}"),
    )
    assert run_cli(tmp_path, selected)["measurements"]["reconstruction"]["status"] == "pass"


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("wrong_step", "step_mismatch"),
        ("wrong_source", "source_trace_mismatch"),
        ("wrong_owner", "candidate_source_mismatch"),
        ("wrong_bytes", "content_digest_mismatch"),
        ("duplicate", "step_ambiguous"),
        ("uncited", "candidate_step_missing"),
        ("legacy_summary", "source_schema_unsupported"),
        ("foreign_turn", "step_mismatch"),
    ],
)
def test_current_producer_rejects_bad_bindings_and_keeps_neighbor(current_selected, mutation, code):
    import copy

    root, selection = current_selected
    neighbor = copy.deepcopy(selection["reconstruction"][0])
    control_records = []
    for record in selection["artifacts"]:
        control_records.append(
            retain(root, "control/" + record["path"], (root / record["path"]).read_bytes(), record["role"])
        )
    neighbor["artifact_id"] = "control/" + neighbor["artifact_id"]
    neighbor["trace_id"] = "control/" + neighbor["trace_id"]
    for key in ("candidate", "source"):
        neighbor[key]["artifact_id"] = "control/" + neighbor[key]["artifact_id"]
    entry = selection["reconstruction"][0]
    if mutation == "wrong_step":
        entry["step"]["pointer"] = "/root_spans/0/turns/1"
    elif mutation == "wrong_source":
        rewrite(
            root, selection, "summary.json", lambda doc: doc["source"].update(safe_path="private/canonical.trace.json")
        )
    elif mutation == "wrong_owner":
        rewrite(root, selection, "summary.json", lambda doc: doc["candidate"].update(instruction="Foreign owner"))
    elif mutation == "legacy_summary":
        rewrite(
            root,
            selection,
            "summary.json",
            lambda doc: doc.update(schema="nemo.eval_author.trace_environment_summary.v3"),
        )
    elif mutation == "uncited":
        rewrite(
            root,
            selection,
            "candidate.json",
            lambda doc: doc["ground_truth"]["artifacts"][0]["provenance"]["turn_ids"].append("unknown-turn"),
        )
        bind_summary_candidate(root, selection)
    else:

        def edit(doc):
            turns = doc["root_spans"][0]["turns"]
            if mutation == "wrong_bytes":
                turns[0]["content"] = "EXACT_PRIVATE_BYTES\n"
            elif mutation == "duplicate":
                turns.append(copy.deepcopy(turns[0]))
            else:
                doc["root_spans"].append({"id": "foreign", "kind": "TOOL", "turns": [copy.deepcopy(turns[0])]})
                entry["step"]["pointer"] = "/root_spans/1/turns/0"

        rewrite(root, selection, "safe/trace.json", edit)
        trace = next(row for row in selection["artifacts"] if row["id"] == "safe/trace.json")
        rewrite(
            root,
            selection,
            "summary.json",
            lambda doc: doc["source"].update(
                safe_sha256="sha256:" + trace["digest"],
                safe_size_bytes=(root / trace["path"]).stat().st_size,
            ),
        )
    selection["artifacts"].extend(control_records)
    selection["reconstruction"].append(neighbor)
    measurement = run_cli(root, selection)["measurements"]["reconstruction"]
    assert measurement["diagnostics"] == [
        {"code": "reconstruction." + code, "subject": "reconstruction:0", "status": "fail"}
    ]
    assert measurement["records"][0]["source_proof"] == "unknown"
    assert measurement["records"][1]["status"] == "pass"


@pytest.mark.parametrize("declaration", ["partial", "generated"])
@pytest.mark.parametrize("content", [None, {"pointer": "/content", "encoding": "utf-8"}])
def test_current_producer_partial_and_generated_never_claim_recovery(current_selected, declaration, content):
    import copy

    root, selection = current_selected
    selection["reconstruction"].append(copy.deepcopy(selection["reconstruction"][0]))
    selection["reconstruction"][0].update(declaration=declaration, content=content)
    measurement = run_cli(root, selection)["measurements"]["reconstruction"]
    assert measurement["status"] == "unknown"
    assert measurement["diagnostics"][0]["code"] == "reconstruction." + declaration + "_source_proof_unknown"
    assert measurement["records"][0]["source_proof"] == "unknown"
    assert measurement["records"][1]["status"] == "pass"


@pytest.mark.parametrize(
    "pointer", ["/root_spans/00/turns/0", "/root_spans/0/turns/-1", "/root_spans/0/children/+0/turns/0"]
)
def test_current_turn_pointer_must_be_canonical(current_selected, pointer):
    root, selection = current_selected
    selection["reconstruction"][0]["step"]["pointer"] = pointer
    measurement = run_cli(root, selection)["measurements"]["reconstruction"]
    assert measurement["diagnostics"][0]["code"] == "reconstruction.step_pointer_invalid"


@pytest.mark.parametrize(
    "mutation,status,code",
    [
        ("nested", "pass", None),
        ("part", "pass", None),
        ("joined", "fail", "content_invalid"),
        ("span_limit", "fail", "trace_steps_invalid"),
    ],
)
def test_current_selected_normalized_structure_is_bounded(current_selected, mutation, status, code):
    root, selection = current_selected
    entry = selection["reconstruction"][0]

    def edit(doc):
        agent = doc["root_spans"][0]
        if mutation == "nested":
            doc["root_spans"] = [{"id": "parent", "kind": "CHAIN", "children": [agent]}]
            entry["step"]["pointer"] = "/root_spans/0/children/0/turns/0"
        elif mutation == "span_limit":
            doc["root_spans"] += [{"id": "unused", "kind": "CHAIN"}] * 2000
        else:
            turn = agent["turns"][0]
            turn["content"] = [{"type": "text", "text": turn["content"]}]
            if mutation == "part":
                entry["content"]["pointer"] = "/content/0/text"

    rewrite(root, selection, "safe/trace.json", edit)
    trace = next(row for row in selection["artifacts"] if row["id"] == "safe/trace.json")
    rewrite(
        root,
        selection,
        "summary.json",
        lambda doc: doc["source"].update(
            safe_sha256="sha256:" + trace["digest"],
            safe_size_bytes=(root / trace["path"]).stat().st_size,
        ),
    )
    measurement = run_cli(root, selection)["measurements"]["reconstruction"]
    assert measurement["status"] == status
    assert measurement["diagnostics"] == (
        []
        if code is None
        else [
            {
                "code": "reconstruction." + code,
                "subject": "reconstruction:0",
                "status": status,
            }
        ]
    )


def test_cli_missing_retained_trace_is_unknown(tmp_path, selected):
    (tmp_path / "private/safe.atif.json").unlink()
    measurement = run_cli(tmp_path, selected)["measurements"]["reconstruction"]
    assert measurement["status"] == "unknown"
    assert measurement["diagnostics"][0]["code"] == "reconstruction.trace_artifact_missing"
