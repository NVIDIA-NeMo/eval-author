# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Behavioral checks for the offline reader using only synthetic evidence."""

import json
import os
import shutil
import stat
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts/render_review.py"
MIB = 1024 * 1024


class Report(HTMLParser):
    """Collect visible content, section membership, and browser-active attributes."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.elements = []
        self.sections = {}
        self.records = {}
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.elements.append((tag, attributes))
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "wbr",
        }:
            self.stack.append((tag, attributes))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if any(tag in {"style", "script"} for tag, _attrs in self.stack):
            return
        self.text.append(data)
        for _tag, attributes in self.stack:
            if section := attributes.get("data-section"):
                self.sections.setdefault(section, []).append(data)
            if record := attributes.get("data-record"):
                self.records.setdefault(record, []).append(data)

    @property
    def visible(self):
        return " ".join(self.text)

    def section(self, name):
        assert name in self.sections, f"Missing {name!r} section in {self.visible[:500]}"
        return " ".join(self.sections[name])


def gym_record(prompt="SYNTHETIC_REQUEST", answer="SYNTHETIC_GRADING_ANSWER"):
    return {
        "responses_create_params": {
            "instructions": "SYNTHETIC_INSTRUCTIONS",
            "input": [{"role": "user", "content": prompt}],
        },
        "expected_answer": answer,
        "custom_grading": {"rule": "SYNTHETIC_GRADING_RULE"},
    }


def atif_record():
    return {
        "schema_version": "ATIF-v1.7",
        "session_id": "synthetic-session",
        "agent": {"name": "synthetic-agent", "version": "1.0"},
        "steps": [
            {"step_id": 1, "source": "user", "message": "SYNTHETIC_ATIF_PROMPT"},
            {
                "step_id": 2,
                "source": "agent",
                "message": "SYNTHETIC_RECORDED_MESSAGE",
                "model_name": "synthetic-model",
                "tool_calls": [
                    {
                        "tool_call_id": "synthetic-call-1",
                        "function_name": "read_fixture",
                        "arguments": {"path": "SYNTHETIC_TOOL_ARGUMENT"},
                    }
                ],
                "observation": {
                    "results": [
                        {
                            "source_call_id": "synthetic-call-1",
                            "content": "<script>SYNTHETIC_TOOL_OBSERVATION</script>",
                            "extra": {"is_error": True},
                        }
                    ]
                },
            },
            {"step_id": 3, "source": "agent", "message": "SYNTHETIC_FINAL_OUTPUT"},
        ],
    }


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def invoke(source, output, *, format="gym-jsonl", rows="1", role="case", script=SCRIPT):
    # Gym JSONL must run without site packages; trace formats need the pinned trace_ingest install.
    isolation = ["-I", "-S"] if format == "gym-jsonl" else ["-I"]
    command = [
        sys.executable,
        *isolation,
        str(script),
        "--input",
        str(source),
        "--format",
        format,
        "--evidence-role",
        role,
        "--output-dir",
        str(output),
    ]
    if rows is not None:
        command.extend(["--rows", rows])
    env = {**os.environ, "MLFLOW_DISABLE_AGENT_HINT": "1"}
    return subprocess.run(command, capture_output=True, text=True, timeout=60, env=env)


def read_report(completed, output, *, records=1):
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert set(summary) == {"status", "report", "records", "issues"}
    assert summary["status"] in {"ok", "attention"}
    assert summary["records"] == records
    assert isinstance(summary["issues"], int)
    assert summary["issues"] >= 0
    assert summary["status"] == ("attention" if summary["issues"] else "ok")
    assert summary["report"] == str((output / "report.html").resolve())
    assert not completed.stderr
    html = (output / "report.html").read_text(encoding="utf-8")
    return summary, Report(html)


def test_case_input_instructions_and_grading_are_separate_and_absent_response_is_explicit(tmp_path):
    source = write_json(tmp_path / "cases.jsonl", gym_record())
    output = tmp_path / "review"
    completed = invoke(source, output)
    summary, report = read_report(completed, output)

    assert summary["status"] == "ok" and summary["issues"] == 0
    request = report.section("request")
    grading = report.section("grading")
    assert "SYNTHETIC_REQUEST" in request and "SYNTHETIC_INSTRUCTIONS" in request
    assert "SYNTHETIC_GRADING_ANSWER" not in request and "SYNTHETIC_GRADING_RULE" not in request
    assert "SYNTHETIC_GRADING_ANSWER" in grading and "SYNTHETIC_GRADING_RULE" in grading
    assert "SYNTHETIC_REQUEST" not in grading
    assert "no response recorded" in report.section("response").lower()
    assert "does not establish execution" in report.section("response").lower()
    assert "SYNTHETIC" not in completed.stdout + completed.stderr
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE((output / "report.html").stat().st_mode) == 0o600


def test_rows_use_physical_lines_and_keep_good_records_amid_blank_malformed_and_missing(tmp_path):
    source = tmp_path / "cases.jsonl"
    source.write_text(
        json.dumps(gym_record("UNSELECTED_FIRST"))
        + "\n\n"
        + json.dumps(gym_record("UNSELECTED_THIRD"))
        + "\n"
        + json.dumps(gym_record("SELECTED_FOURTH"))
        + '\n{"broken": "SYNTHETIC_INVALID_PAYLOAD"\n',
        encoding="utf-8",
    )
    output = tmp_path / "review"
    completed = invoke(source, output, rows="2,4-6")
    summary, report = read_report(completed, output, records=4)

    assert summary["status"] == "attention" and summary["issues"] >= 3
    assert set(report.records) == {"2", "4", "5", "6"}
    assert "SELECTED_FOURTH" in " ".join(report.records["4"])
    assert "UNSELECTED_FIRST" not in report.visible and "UNSELECTED_THIRD" not in report.visible
    assert "SYNTHETIC_INVALID_PAYLOAD" in report.visible
    assert "SYNTHETIC_INVALID_PAYLOAD" not in completed.stdout + completed.stderr
    assert "missing" in " ".join(report.records["6"]).lower()


def test_rollout_keeps_request_output_pairing_reward_zero_and_failure_details(tmp_path):
    record = gym_record()
    record.update(
        {
            "response": {
                "status": "failed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "synthetic-call-id",
                        "name": "read_fixture",
                        "arguments": '{"path":"SYNTHETIC_ARGUMENT"}',
                    },
                    {"type": "function_call_output", "call_id": "synthetic-call-id", "output": "SYNTHETIC_RESULT"},
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "SYNTHETIC_AGENT_OUTPUT"}],
                    },
                ],
                "error": {"code": "synthetic_error", "message": "SYNTHETIC_REPORTED_ERROR"},
            },
            "reward": 0,
            "failure_reason": "SYNTHETIC_FAILURE_REASON",
            "_ng_task_index": 31,
            "_ng_rollout_index": 2,
            "unexpected_field": "SYNTHETIC_UNKNOWN_FIELD",
        }
    )
    source = write_json(tmp_path / "rollouts.jsonl", record)
    output = tmp_path / "review"
    _summary, report = read_report(invoke(source, output, role="agent"), output)

    assert "SYNTHETIC_AGENT_OUTPUT" not in report.section("request")
    assert "SYNTHETIC_REQUEST" not in report.section("response")
    for value in (
        "synthetic-call-id",
        "SYNTHETIC_ARGUMENT",
        "SYNTHETIC_RESULT",
        "SYNTHETIC_AGENT_OUTPUT",
        "SYNTHETIC_REPORTED_ERROR",
    ):
        assert value in report.section("response")
    for value in ("SYNTHETIC_FAILURE_REASON", "SYNTHETIC_UNKNOWN_FIELD", "_ng_task_index", "_ng_rollout_index"):
        assert value in report.visible
    results = report.section("results")
    assert "reward" in results.lower() and "0" in results


@pytest.mark.parametrize(
    ("role", "label"),
    [
        ("case", "Case specification"),
        ("source", "Source provenance"),
        ("control", "Task control"),
        ("agent", "Actual-agent evidence"),
    ],
)
def test_caller_selected_evidence_role_is_visible(tmp_path, role, label):
    source = write_json(tmp_path / "cases.jsonl", gym_record())
    output = tmp_path / "review"
    summary, report = read_report(invoke(source, output, role=role), output)
    assert label in " ".join(report.records["1"])
    assert summary["status"] == ("attention" if role in {"agent", "control"} else "ok")


def test_atif_trace_renders_spans_depth_first_with_tool_results_and_error_flags(tmp_path):
    source = write_json(tmp_path / "source.atif.json", atif_record())
    output = tmp_path / "review"
    summary, report = read_report(invoke(source, output, format="atif", rows=None, role="source"), output)
    spans = report.section("spans")
    order = [
        "SYNTHETIC_ATIF_PROMPT",
        "SYNTHETIC_RECORDED_MESSAGE",
        "read_fixture",
        "SYNTHETIC_TOOL_ARGUMENT",
        "<script>SYNTHETIC_TOOL_OBSERVATION</script>",
        "SYNTHETIC_FINAL_OUTPUT",
    ]
    positions = [spans.index(value) for value in order]
    assert positions == sorted(positions)
    assert "synthetic-model" in spans
    assert "is_error: True" in spans
    assert "ATIF tool result reported is_error" in spans
    assert summary["status"] == "attention"
    assert not any(tag in {"script", "img"} for tag, _attrs in report.elements)


def test_mlflow_export_renders_instruction_and_tool_name(tmp_path):
    source = SCRIPT.parents[3] / "skills/mlflow-to-atif/evals/files/valid.json"
    output = tmp_path / "review"
    _summary, report = read_report(invoke(source, output, format="mlflow", rows=None, role="source"), output)
    spans = report.section("spans")
    assert "Where is Paris?" in spans
    assert "TOOL · lookup" in spans
    assert "is_error: False" in spans


@pytest.mark.parametrize("format", ["gym-jsonl", "atif", "mlflow"])
def test_missing_explicit_input_produces_honest_review_artifact(tmp_path, format):
    output = tmp_path / "review"
    completed = invoke(tmp_path / "missing.json", output, format=format, rows="1" if format == "gym-jsonl" else None)
    summary, report = read_report(completed, output)
    assert summary["status"] == "attention" and summary["issues"] >= 1
    assert "missing" in report.visible.lower()


@pytest.mark.parametrize(
    ("format", "record"),
    [
        ("gym-jsonl", []),
        ("gym-jsonl", {"responses_create_params": []}),
        (
            "gym-jsonl",
            {
                "responses_create_params": {
                    "input": [{"role": "user", "content": [{"type": [], "text": "SYNTHETIC_BAD_TYPE"}]}]
                }
            },
        ),
        ("atif", {"schema_version": []}),
        ("atif", {"schema_version": "ATIF-v1.7", "steps": [7]}),
        ("mlflow", {"traces": "SYNTHETIC_BAD_TRACES"}),
    ],
)
def test_malformed_json_shapes_are_visible_instead_of_crashing(tmp_path, format, record):
    source = write_json(tmp_path / "malformed.json", record)
    output = tmp_path / "review"
    completed = invoke(source, output, format=format, rows="1" if format == "gym-jsonl" else None)
    summary, report = read_report(completed, output)
    assert summary["status"] == "attention" and summary["issues"] >= 1
    assert any(word in report.visible.lower() for word in ("malformed", "unsupported", "no usable"))
    assert "SYNTHETIC_BAD" not in completed.stdout + completed.stderr


@pytest.mark.parametrize(
    ("malformed", "notice"),
    [
        (b'{"data":"SYNTHETIC_INVALID_UTF8\xff"}', "malformed"),
        (b'{"reward":0,"reward":1}', "malformed"),
        (b'{"reward":NaN}', "malformed"),
        (b'{"reward":1e999}', "malformed"),
        (b'{"value":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}", "structure"),
        (b'{"value":[' + b",".join([b"0"] * 20001) + b"]}", "structure"),
    ],
    ids=[
        "invalid-utf8",
        "duplicate-keys",
        "non-finite-number",
        "overflowing-number",
        "excessive-nesting",
        "excessive-values",
    ],
)
def test_malformed_or_excessive_records_do_not_hide_other_selected_rows(tmp_path, malformed, notice):
    source = tmp_path / "mixed.jsonl"
    source.write_bytes(malformed + b"\n" + json.dumps(gym_record("SYNTHETIC_VALID_SECOND")).encode("utf-8") + b"\n")
    output = tmp_path / "review"
    completed = invoke(source, output, rows="1-2")
    summary, report = read_report(completed, output, records=2)
    assert summary["status"] == "attention" and summary["issues"] >= 1
    assert notice in " ".join(report.records["1"]).lower()
    assert "SYNTHETIC_VALID_SECOND" in " ".join(report.records["2"])
    assert "SYNTHETIC_INVALID_UTF8" not in completed.stdout + completed.stderr
    if notice == "structure":
        assert (output / "report.html").stat().st_size < len(malformed) * 2 + 20000


def test_html_payload_is_inert_and_embedded_source_paths_are_never_followed(tmp_path):
    private_source = tmp_path / "not-selected.json"
    private_source.write_text("SYNTHETIC_DO_NOT_READ_SECRET", encoding="utf-8")
    payload = (
        '</pre><script>syntheticPayload()</script><img src="https://invalid.example/x" onerror="syntheticPayload()">'
    )
    record = gym_record(payload)
    record["atif_conversion"] = {"source_trajectory_paths": [str(private_source)]}
    record["untrusted_link"] = "javascript:syntheticPayload()"
    source = write_json(tmp_path / "selected.jsonl", record)
    output = tmp_path / "review"
    completed = invoke(source, output)
    _summary, report = read_report(completed, output)

    assert payload in report.visible
    assert "SYNTHETIC_DO_NOT_READ_SECRET" not in report.visible
    assert "syntheticPayload" not in completed.stdout + completed.stderr
    assert any(attrs.get("href") == source.as_uri() for _tag, attrs in report.elements)
    assert not any(
        tag in {"script", "img", "iframe", "object", "embed", "base", "link"} for tag, _attrs in report.elements
    )
    for _tag, attrs in report.elements:
        assert not any(name.startswith("on") for name in attrs)
        assert not any(name in attrs for name in ("src", "srcdoc", "action", "formaction"))
        if href := attrs.get("href"):
            assert href.startswith("#") or href == source.as_uri()


def test_copied_single_script_runs_without_site_packages_or_repository_imports(tmp_path):
    copied = tmp_path / "copied-reader.py"
    shutil.copyfile(SCRIPT, copied)
    source = write_json(tmp_path / "cases.jsonl", gym_record())
    output = tmp_path / "review"
    _summary, report = read_report(invoke(source, output, script=copied), output)
    assert "SYNTHETIC_REQUEST" in report.section("request")


@pytest.mark.parametrize("rows", [None, "", "0", "-1", "1-", "2-1", "abc", "1,,2"])
def test_invalid_or_missing_gym_row_selection_rejected_without_writing(tmp_path, rows):
    source = write_json(tmp_path / "cases.jsonl", gym_record())
    output = tmp_path / "review"
    completed = invoke(source, output, rows=rows)
    assert completed.returncode == 2
    assert not output.exists()
    assert "SYNTHETIC" not in completed.stdout + completed.stderr


def test_atif_rejects_row_selection(tmp_path):
    source = write_json(tmp_path / "source.atif.json", atif_record())
    output = tmp_path / "review"
    assert invoke(source, output, format="atif").returncode == 2
    assert not output.exists()


@pytest.mark.parametrize("role", [None, "guessed"])
def test_evidence_role_is_required_and_constrained(tmp_path, role):
    source = write_json(tmp_path / "cases.jsonl", gym_record())
    output = tmp_path / "review"
    command = [
        sys.executable,
        "-S",
        str(SCRIPT),
        "--input",
        str(source),
        "--format",
        "gym-jsonl",
        "--rows",
        "1",
        "--output-dir",
        str(output),
    ]
    if role is not None:
        command.extend(["--evidence-role", role])
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 2
    assert not output.exists()


@pytest.mark.parametrize("collision", ["directory", "symlink", "file"])
def test_existing_output_or_symlink_is_not_overwritten(tmp_path, collision):
    source = write_json(tmp_path / "cases.jsonl", gym_record())
    output = tmp_path / "review"
    if collision == "file":
        output.write_text("SYNTHETIC_EXISTING_OUTPUT", encoding="utf-8")
        sentinel = output
    else:
        real_output = tmp_path / "original" if collision == "symlink" else output
        real_output.mkdir()
        sentinel = real_output / "report.html"
        sentinel.write_text("SYNTHETIC_EXISTING_OUTPUT", encoding="utf-8")
        if collision == "symlink":
            output.symlink_to(real_output, target_is_directory=True)
    completed = invoke(source, output)
    assert completed.returncode == 2
    assert sentinel.read_text(encoding="utf-8") == "SYNTHETIC_EXISTING_OUTPUT"


@pytest.mark.parametrize("limit", ["input", "record", "selected", "rows"])
def test_bounded_inputs_fail_before_creating_report(tmp_path, limit):
    source = tmp_path / "cases.jsonl"
    rows = "1"
    if limit == "input":
        with source.open("wb") as stream:
            stream.truncate(32 * MIB + 1)
    elif limit == "record":
        source.write_text(json.dumps(gym_record("x" * (2 * MIB))), encoding="utf-8")
    elif limit == "selected":
        source.write_text((json.dumps(gym_record("x" * (7 * MIB // 4))) + "\n") * 5, encoding="utf-8")
        rows = "1-5"
    else:
        source.write_text((json.dumps(gym_record()) + "\n") * 201, encoding="utf-8")
        rows = "1-201"
    output = tmp_path / "review"
    completed = invoke(source, output, rows=rows)
    assert completed.returncode == 2
    assert not output.exists()
    assert "SYNTHETIC" not in completed.stdout + completed.stderr
