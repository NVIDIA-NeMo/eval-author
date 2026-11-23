# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for frozen workplace source observations and conformance accounting."""

import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE = ROOT / "skills/eval-author-trace-environment/examples/workplace/conformance"
SPEC = importlib.util.spec_from_file_location("workplace_conformance_runner", CONFORMANCE / "runner.py")
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@pytest.fixture
def corpus(tmp_path):
    path = tmp_path / "corpus"
    shutil.copytree(CONFORMANCE / "corpus-v1", path)
    return path


def test_frozen_session_split_and_observed_state_chains():
    manifest = runner.validate_corpus()
    assert len(manifest["sessions"]) == 10
    assert {item["split"] for item in manifest["sessions"]} == {"development", "heldout"}
    assert len({item["session_id"] for item in manifest["sessions"]}) == 10
    assert len({item["seed_sha256"] for item in manifest["sessions"]}) == 10
    assert "Not the requested Oct30 corpus" in manifest["disclosure"]


@pytest.mark.parametrize(
    "relative",
    [
        "freeze.json",
        "provenance.json",
        "schemas.json",
        "heldout/heldout-search-v1.inputs.json",
        "development/development-reply-v1.observations.json",
    ],
)
def test_tampering_fails_closed(corpus, relative):
    with (corpus / relative).open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="hash mismatch|tampering"):
        runner.validate_corpus(corpus)


def test_replay_builder_never_reads_heldout(corpus, tmp_path, monkeypatch):
    original = Path.read_text
    reads = []

    def guarded(path, *args, **kwargs):
        reads.append(path)
        assert "heldout" not in path.parts
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    receipt = runner.compile_replay(corpus, tmp_path / "scenario.json")
    assert len(receipt["training_sources"]) == 5
    assert receipt["conflicts_unsupported"]
    scenario = runner.load(tmp_path / "scenario.json")
    for conflict in receipt["conflicts_unsupported"]:
        tool = next(tool for tool in scenario["tools"] if tool["definition"]["name"] == conflict["name"])
        assert all(case["arguments"] != conflict["arguments"] for case in tool["cases"])
    assert reads


def test_replay_rejects_disguised_heldout_training(corpus, tmp_path):
    path = corpus / "development/development-search-v1.observations.json"
    data = runner.load(path)
    data["split"] = "heldout"
    runner.save(path, data)
    with pytest.raises(ValueError, match="Heldout"):
        runner.compile_replay(corpus, tmp_path / "scenario.json")


def test_exact_runtime_uses_development_and_fails_closed(tmp_path):
    scenario = tmp_path / "scenario.json"
    runner.compile_replay(CONFORMANCE / "corpus-v1", scenario)
    runtime = runner.Replay(scenario)
    try:
        known = runtime.call(
            {"name": "email_get_email_information_by_id", "arguments": {"email_id": "999999", "field": "body"}}
        )
        assert known == {"output": "Email not found.", "unsupported": False}
        unknown = runtime.call({"name": "email_reply_email", "arguments": {"email_id": "800", "body": "Heldout"}})
        assert unknown["unsupported"] is True
    finally:
        runtime.close()


def event():
    return {
        "name": "email_reply_email",
        "label": "reply",
        "category": "supported",
        "output": "Email replied successfully.",
        "exception": None,
        "after_state": [{"email_id": "9"}],
    }


def test_rates_preserve_denominator_and_separate_dimensions():
    expected = event()
    rows = [
        runner.compare(expected, {"output": expected["output"], "unsupported": False}, expected["after_state"]),
        runner.compare(expected, {"output": "Wrong", "unsupported": False}, []),
        runner.compare(expected, {"output": None, "unsupported": True}, None),
    ]
    result = runner.rates(rows)["email_reply_email"]
    assert result["denominator"] == 3
    assert (result["matched"], result["mismatch"], result["unsupported"]) == (1, 1, 1)
    assert result["matched_rate"] == pytest.approx(1 / 3)
    assert result["dimensions"]["output_schema"]["matched"] == 2
    assert result["dimensions"]["error_semantics"]["mismatch"] == 1
    assert result["dimensions"]["state"] == {"matched": 1, "mismatch": 1, "unsupported": 0, "unavailable": 1}
    assert runner.rates([])["email_reply_email"]["matched_rate"] is None


def test_mutation_mismatch_is_not_hidden_by_matching_output():
    expected = event()
    row = runner.compare(expected, {"output": expected["output"], "unsupported": False}, [])
    assert row["output"] == "matched"
    assert row["state"] == "mismatch"


@pytest.mark.parametrize(
    "response", [{"output": None}, {"output": None, "unsupported": "false"}, {"unsupported": False}]
)
def test_invalid_service_envelope_is_not_accepted(response):
    with pytest.raises(ValueError, match="Service must return"):
        runner.compare(event(), response, None)


def test_actual_source_observations_preserve_repeated_mutations():
    observed = runner.load(CONFORMANCE / "corpus-v1/heldout/heldout-reply-v1.observations.json")
    events = {item["label"]: item for item in observed["events"]}
    assert events["first-reply"]["output"] == "Email replied successfully."
    assert len(events["first-reply"]["after_state"]) == 7
    assert len(events["repeat-reply"]["after_state"]) == 8
    assert events["new-reply-time"]["output"] == {"sent_datetime": "2023-11-30 23:59:00"}
    assert events["nested-subject"]["output"] == {"subject": "RE: RE: Cedar release"}
    assert events["empty-body"]["before_state"] == events["empty-body"]["after_state"]


@pytest.mark.parametrize("mode", ["wrong-output", "all-unsupported", "unavailable-state"])
def test_failed_after_comparison_exits_nonzero(tmp_path, monkeypatch, mode):
    import contextlib

    source = tmp_path / "source"
    source.mkdir()
    entrypoint = source / "service.py"
    entrypoint.write_text("# Test service entrypoint\n")
    work = tmp_path / "work"
    work.mkdir()
    # The runner follows the frozen manifest's session order, not lexical path order.
    manifest = runner.load(CONFORMANCE / "corpus-v1/freeze.json")
    events = iter(
        event
        for item in manifest["sessions"]
        if item["split"] == "heldout"
        for event in runner.load(
            CONFORMANCE / "corpus-v1" / item["inputs"].replace(".inputs.json", ".observations.json")
        )["events"]
    )
    launches = []

    @contextlib.contextmanager
    def fake_session(service, *args):
        launches.append(service)
        assert service != entrypoint
        assert service.read_text() == "# Test service entrypoint\n"
        yield "http://unused", work

    def fake_http(*args):
        expected = next(events)
        if mode != "unavailable-state":
            runner.save(work / "state.json", {"state": expected["after_state"]})
        if mode == "all-unsupported":
            return {"output": None, "unsupported": True, "error": {"code": "invalid_arguments"}}
        if mode == "unavailable-state":
            return {"output": expected["output"], "exception": expected["exception"], "unsupported": False}
        return {"output": "Deliberate test mismatch", "unsupported": False}

    def fake_schema(service, *args):
        launches.append(service)
        entrypoint.write_text("# Changed after capture\n")
        return {"status": "captured", "validation_semantics_match": True, "exact_definition_match": True}

    monkeypatch.setattr(runner, "service_session", fake_session)
    monkeypatch.setattr(runner, "http_json", fake_http)
    monkeypatch.setattr(runner, "capture_mcp_schema", fake_schema)
    report = tmp_path / "report.json"
    assert runner.run(CONFORMANCE / "corpus-v1", report, work, entrypoint, "python", "state.json") == 1
    result = runner.load(report)
    assert result["unexpected_after_mismatches"] > 0
    assert len(launches) == 6 and len(set(launches)) == 1
    assert result["runtime_snapshot"]["captured_before_execution"] is True
    if mode == "all-unsupported":
        assert all(row["state"] == "matched" for row in result["after_cases"])
    if mode == "unavailable-state":
        assert result["unexpected_after_mismatches"] == 56


def test_support_predicate_uses_matching_prestate_not_labels():
    schemas = runner.load(CONFORMANCE / "corpus-v1/schemas.json")
    observed = runner.load(CONFORMANCE / "corpus-v1/heldout/heldout-reply-v1.observations.json")
    tied = next(item for item in observed["events"] if item["label"] == "after-replies")
    assert runner.allowed_unsupported(tied, schemas) == "date_ties"
    tied["label"] = "renamed"
    tied["arguments"] = {"query": "not-present"}
    assert runner.allowed_unsupported(tied, schemas) is None
    tied["arguments"] = {"query": "", "date_min": "2024-01-01"}
    assert runner.allowed_unsupported(tied, schemas) == "date_ties"


def test_unsupported_code_must_match_independent_boundary():
    row = runner.compare(
        event(), {"output": None, "unsupported": True, "error": {"code": "date_ties"}}, event()["after_state"]
    )
    row["allowed_unsupported_code"] = "date_ties"
    assert runner.after_failure(row) is False
    row["allowed_unsupported_code"] = "page_size"
    assert runner.after_failure(row) is True
    row["allowed_unsupported_code"] = None
    assert runner.after_failure(row) is True


def test_runtime_capture_excludes_bytecode_and_preserves_bytes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "service.py").write_text("# captured\n")
    (source / "__pycache__").mkdir()
    (source / "__pycache__/service.pyc").write_bytes(b"untrusted-bytecode")
    (source / "configuration.json").write_text("{}")
    entrypoint, receipt = runner.capture_runtime(source / "service.py", tmp_path)
    (source / "service.py").write_text("# changed\n")
    assert entrypoint.read_text() == "# captured\n"
    assert set(receipt["files"]) == {"service.py", "configuration.json"}
    assert runner.sha(entrypoint) == receipt["files"]["service.py"]
    assert not (entrypoint.parent / "__pycache__").exists()
    assert entrypoint.stat().st_mode & 0o222 == 0


def test_service_launch_is_loopback_only(tmp_path, monkeypatch):
    import contextlib

    commands = []

    class Process:
        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, **kwargs):
            return 0

    def popen(command, **kwargs):
        commands.append(command)
        return Process()

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(runner.socket, "create_connection", lambda *args, **kwargs: contextlib.nullcontext())
    with runner.service_session(Path("service.py"), [], tmp_path, "python"):
        pass
    assert commands[0][commands[0].index("--host") + 1] == "127.0.0.1"
    assert "-B" in commands[0]
