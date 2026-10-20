# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Multi-trace tool-call recording: verbatim fields, scoped schemas, and an all-or-nothing private import."""

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from fixture_compiler import json_sha256  # noqa: E402
from tool_corpus import MEMBERS_SCHEMA, extract_source  # noqa: E402

LOOKUP = {"name": "lookup", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}}}


def trace(**overrides):
    payload = {
        "schema_version": "ATIF-v1.7",
        "session_id": "session-1",
        "trajectory_id": "trajectory-1",
        "agent": {"name": "synthetic", "version": "2.1", "tool_definitions": [LOOKUP]},
        "steps": [
            {"step_id": 1, "source": "user", "message": "Find a and b."},
            {
                "step_id": 2,
                "source": "agent",
                "timestamp": "2026-10-20T10:00:00Z",
                "message": "",
                "tool_calls": [
                    {"tool_call_id": "a", "function_name": "lookup", "arguments": {"id": "a"}, "extra": {"k": 1}},
                    {"tool_call_id": "b", "function_name": "lookup", "arguments": {"id": "b"}},
                    {"tool_call_id": "c", "function_name": "lookup", "arguments": {"id": "c"}},
                ],
                "observation": {
                    "results": [
                        {"source_call_id": "b", "content": "B1", "extra": {"is_error": True}},
                        {"source_call_id": "a", "content": "A"},
                        {"source_call_id": "b", "content": "B2"},
                        {"source_call_id": None, "content": "unlinked"},
                    ]
                },
            },
            {
                "step_id": 3,
                "source": "agent",
                "message": "",
                "tool_calls": [{"tool_call_id": "a", "function_name": "lookup", "arguments": {"id": "again"}}],
                "observation": {"results": [{"source_call_id": "a", "content": "A2"}]},
            },
        ],
    }
    payload.update(overrides)
    return payload


def extract(payload):
    return extract_source(payload, source_sha256="sha256:src", path="t.json", source_kind="atif", selection="s")


def test_records_each_call_in_order_with_only_its_own_same_step_results():
    source, calls = extract(trace())

    assert [(c["step_id"], c["call_index"], c["ordinal"], c["tool_call_id"]) for c in calls] == [
        (2, 0, 1, "a"),
        (2, 1, 2, "b"),
        (2, 2, 3, "c"),
        (3, 0, 4, "a"),
    ]
    first, second, third, reused = calls
    assert first["arguments"] == {"id": "a"} and first["call_extra"] == {"k": 1}
    assert first["timestamp"] == "2026-10-20T10:00:00Z" and reused["timestamp"] is None
    assert [r["result"]["content"] for r in first["results"]] == ["A"]
    # Out-of-order, multiple, and missing results stay exactly as recorded.
    assert [(r["result_index"], r["result"]["content"], r["error_status"]) for r in second["results"]] == [
        (0, "B1", "error"),
        (2, "B2", "unrecorded"),
    ]
    assert third["results"] == []
    # A reused call ID in a later step never borrows an earlier step's result.
    assert [r["result"]["content"] for r in reused["results"]] == ["A2"]
    assert "unlinked" not in json.dumps(calls)

    assert source["trajectories"] == [
        {
            "trajectory_path": "$",
            "session_id": "session-1",
            "trajectory_id": "trajectory-1",
            "agent": {"name": "synthetic", "version": "2.1"},
            "tool_definitions": {json_sha256(LOOKUP): LOOKUP},
        }
    ]


def test_subagent_calls_keep_their_own_identity_order_and_definitions():
    child = trace(session_id="child-session", trajectory_id=None, agent={"name": "child"})
    source, calls = extract(trace(subagent_trajectories=[child]))

    child_calls = [c for c in calls if c["trajectory_path"] == "$.subagent_trajectories[0]"]
    assert [c["ordinal"] for c in child_calls] == [1, 2, 3, 4]
    # The parent's declared definition is not borrowed by the child.
    assert {c["schema_status"] for c in child_calls} == {"undeclared"}
    assert source["trajectories"][1]["session_id"] == "child-session"
    assert source["trajectories"][1]["trajectory_id"] is None


@pytest.mark.parametrize(
    ("definitions", "call_extra", "expected"),
    [
        ([LOOKUP], None, "declared"),
        (
            [LOOKUP, {"type": "function", "function": {"name": "lookup", "parameters": LOOKUP["inputSchema"]}}],
            None,
            "declared",
        ),
        ([LOOKUP, {"name": "lookup", "inputSchema": {"type": "object"}}], None, "ambiguous"),
        ([{**LOOKUP, "extra": {"tool_scope": {"server": "one"}}}], None, "undeclared"),
        ([{**LOOKUP, "extra": {"tool_scope": {"server": "one"}}}], {"tool_scope": {"server": "one"}}, "declared"),
        ([], None, "undeclared"),
    ],
)
def test_schema_version_resolves_only_within_name_and_server_scope(definitions, call_extra, expected):
    payload = trace(agent={"name": "synthetic", "tool_definitions": definitions})
    payload["steps"][1]["tool_calls"][0]["extra"] = call_extra
    source, calls = extract(payload)

    status, digest = calls[0]["schema_status"], calls[0]["schema_sha256"]
    assert status == expected
    if expected == "declared":
        assert digest in source["trajectories"][0]["tool_definitions"]
    else:
        assert digest is None


def cli(*args):
    run = subprocess.run(
        [sys.executable, str(SCRIPTS / "trace_environment.py"), *map(str, args)], capture_output=True, text=True
    )
    return run.returncode, json.loads(run.stdout)


def write_members(tmp_path, members, **overrides):
    manifest = {"schema": MEMBERS_SCHEMA, "selection": "nightly agent runs, 2026-10", "members": members}
    manifest.update(overrides)
    path = tmp_path / "members.json"
    path.write_text(json.dumps(manifest))
    return path


def two_sources(tmp_path):
    (tmp_path / "traces").mkdir()
    (tmp_path / "traces/first.json").write_text(json.dumps(trace()))
    second = tmp_path / "second.json"
    second.write_text(json.dumps(trace(session_id="session-2")))
    return [{"atif": "traces/first.json", "source_kind": "atif"}, {"atif": str(second), "source_kind": "mlflow"}]


def test_import_writes_a_complete_private_corpus_that_reproduces_byte_for_byte(tmp_path):
    members = write_members(tmp_path, two_sources(tmp_path))

    code, first = cli("corpus-import", "--members", members, "--corpus-dir", tmp_path / "one")
    assert code == 0, first
    assert cli("corpus-import", "--members", members, "--corpus-dir", tmp_path / "two")[0] == 0

    one, two = Path(first["corpus_dir"]), tmp_path / "two"
    for name in ("manifest.json", "calls.jsonl"):
        assert (one / name).read_bytes() == (two / name).read_bytes()
    manifest = json.loads((one / "manifest.json").read_text())
    calls = [json.loads(line) for line in (one / "calls.jsonl").read_text().splitlines()]
    raw_first = (tmp_path / "traces/first.json").read_bytes()

    assert first["counts"] == manifest["counts"] == {"sources": 2, "trajectories": 2, "calls": 8}
    assert manifest["calls_sha256"] == "sha256:" + hashlib.sha256((one / "calls.jsonl").read_bytes()).hexdigest()
    assert [(s["path"], s["source_kind"], s["selection"]) for s in manifest["sources"]] == [
        ("traces/first.json", "atif", "nightly agent runs, 2026-10"),
        (str(tmp_path / "second.json"), "mlflow", "nightly agent runs, 2026-10"),
    ]
    assert manifest["sources"][0]["source_sha256"] == "sha256:" + hashlib.sha256(raw_first).hexdigest()
    assert [c["source_sha256"] for c in calls] == [s["source_sha256"] for s in manifest["sources"] for _ in range(4)]
    if os.name == "posix":
        assert stat.S_IMODE(one.stat().st_mode) == 0o700
        assert {stat.S_IMODE(path.stat().st_mode) for path in one.iterdir()} == {0o600}


@pytest.mark.parametrize(
    ("case", "error"),
    [
        ("existing_dir", "already exists"),
        ("duplicate_bytes", "same bytes as an earlier member"),
        ("duplicate_path", "more than once"),
        ("invalid_atif", "member 1 is not valid ATIF: .*step_id must be sequential"),
        ("unknown_kind", "source_kind is not recognized"),
        ("empty_selection", "selection must be nonempty"),
        ("symlinked_member", "must be a regular file"),
    ],
)
def test_import_rejects_bad_input_without_leaving_a_partial_corpus(tmp_path, case, error):
    members = two_sources(tmp_path)
    overrides = {}
    corpus = tmp_path / "out/corpus"
    if case == "existing_dir":
        corpus.mkdir(parents=True)
    elif case == "duplicate_bytes":
        (tmp_path / "copy.json").write_bytes((tmp_path / "traces/first.json").read_bytes())
        members.append({"atif": "copy.json", "source_kind": "atif"})
    elif case == "duplicate_path":
        members.append(dict(members[0]))
    elif case == "invalid_atif":
        broken = trace()
        broken["steps"][1]["step_id"] = 5
        Path(members[1]["atif"]).write_text(json.dumps(broken))
    elif case == "unknown_kind":
        members[0]["source_kind"] = "csv"
    elif case == "empty_selection":
        overrides["selection"] = " "
    elif case == "symlinked_member":
        (tmp_path / "link.json").symlink_to(tmp_path / "traces/first.json")
        members[0]["atif"] = "link.json"

    code, result = cli(
        "corpus-import", "--members", write_members(tmp_path, members, **overrides), "--corpus-dir", corpus
    )

    assert code == 1
    assert re.search(error, result["error"]), result
    leftovers = sorted(path.name for path in corpus.parent.iterdir()) if corpus.parent.exists() else []
    assert leftovers == (["corpus"] if case == "existing_dir" else [])
    if case == "existing_dir":
        assert list(corpus.iterdir()) == []
