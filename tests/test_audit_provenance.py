# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Code provenance counts copied tool-call lines next to, never inside, audit coverage."""

from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from audit_fixtures import bound_audit, bound_gap
from test_audit_report_content import EXAMPLE, SCRIPTS, example_data, save

PROVENANCE = SCRIPTS / "provenance.py"
SKILL_TEXT = """# Demo skill

```python
import json
def summarize(rows):
    return sorted(rows, key=lambda row: row["score"])
```
"""
PROMPT_TEXT = """Write totals with:
total = sum(row["amount"] for row in rows)
return sorted(rows, key=lambda row: row["score"])
"""
HEREDOC = """cat <<'EOF' > report.py
import json
# build the report
def summarize(rows):
    return sorted(rows, key=lambda row: row["score"])
}
print(summarize(load_rows()))
EOF"""


def call(call_id: str, name: str, arguments: dict) -> dict:
    return {"tool_call_id": call_id, "function_name": name, "arguments": arguments}


def write_trace(path: Path, calls: list[dict], sub_calls: list[dict] | None = None, trajectory: str = "run-1") -> Path:
    payload: dict = {
        "schema_version": "ATIF-v1.7",
        "session_id": "session-1",
        "trajectory_id": trajectory,
        "agent": {"name": "demo-agent", "version": "1"},
        "steps": [
            {"step_id": 1, "source": "user", "message": "Build the report."},
            {"step_id": 2, "source": "agent", "message": "Working.", "tool_calls": calls},
        ],
    }
    if sub_calls:
        payload["subagent_trajectories"] = [
            {
                "schema_version": "ATIF-v1.7",
                "session_id": "session-1",
                "trajectory_id": "helper",
                "agent": {"name": "helper", "version": "1"},
                "steps": [{"step_id": 1, "source": "agent", "message": "Helping.", "tool_calls": sub_calls}],
            }
        ]
    path.write_text(json.dumps(payload))
    return path


def write_sources(root: Path) -> tuple[Path, Path]:
    skills = root / "skills"
    (skills / "demo").mkdir(parents=True)
    (skills / "demo/SKILL.md").write_text(SKILL_TEXT)
    (skills / "demo/logo.png").write_bytes(b"\x89PNG\x00\xff")
    (skills / ".hidden").mkdir()
    (skills / ".hidden/notes.md").write_text("result = compute_secret_total(values)\n")
    prompt = root / "prompt.md"
    prompt.write_text(PROMPT_TEXT)
    return skills, prompt


def mixed_trace(path: Path) -> Path:
    content = 'total = sum(row["amount"] for row in rows)\nresult = compute_secret_total(values)\n'
    return write_trace(
        path,
        [
            call("c1", "bash", {"command": HEREDOC}),
            call("c2", "write", {"input": json.dumps({"path": "totals.py", "content": content})}),
            call("c3", "read", {"path": "README"}),
        ],
        [
            call(
                "s1",
                "python",
                {
                    "code": '        return sorted(rows,   key=lambda row: row["score"])\nvalues = [row.amount for row in rows]'
                },
            )
        ],
    )


def run(script: Path, *args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True)


def provenance(trace: Path, out: Path, skills: Path, prompt: Path, *extra: str) -> dict:
    result = run(
        PROVENANCE,
        "--trace",
        trace,
        "--source",
        f"skills={skills}",
        "--source",
        f"prompt={prompt}",
        "--out-dir",
        out,
        *extra,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(Path(json.loads(result.stdout)["provenance"][0]["path"]).read_text())


def test_copied_lines_are_attributed_per_source_and_tool_call(tmp_path: Path) -> None:
    skills, prompt = write_sources(tmp_path)
    trace = mixed_trace(tmp_path / "trace.json")

    report = provenance(trace, tmp_path / "out", skills, prompt, "--task-id", "demo")

    assert report["totals"] == {
        "tool_call_count": 4,
        "authored_lines": 8,
        "copied_lines": 4,
        "copied_share": 0.5,
        "matched_lines_by_source": {"skills": 3, "prompt": 3},
    }
    rows = [
        (row["span_id"], row.get("tool_call_id"), row["authored_lines"], row["copied_lines"])
        for row in report["tool_calls"]
    ]
    # Imports, comments, braces, heredoc delimiters, and short values are not authored code;
    # the hidden directory's line stays original, and the read call authored nothing.
    assert rows == [
        ("trajectory/step/2/tool/0", "c1", 4, 2),
        ("trajectory/step/2/tool/1", "c2", 2, 1),
        ("trajectory/subagent/0/step/1/tool/0", "s1", 2, 1),
    ]
    assert [row["matched_lines_by_source"] for row in report["tool_calls"]] == [
        {"skills": 2, "prompt": 1},
        {"prompt": 1},
        {"skills": 1, "prompt": 1},
    ]
    skill_sha = hashlib.sha256(SKILL_TEXT.encode()).hexdigest()
    assert report["binding"] == {
        "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
        "sources": [
            {
                "name": "skills",
                "path": str(skills.resolve()),
                "kind": "directory",
                "sha256": hashlib.sha256(f"{skill_sha}  demo/SKILL.md\n".encode()).hexdigest(),
                "file_count": 1,
            },
            {
                "name": "prompt",
                "path": str(prompt.resolve()),
                "kind": "file",
                "sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
                "file_count": 1,
            },
        ],
    }


def test_trace_with_only_trivial_lines_has_no_authored_code(tmp_path: Path) -> None:
    skills, prompt = write_sources(tmp_path)
    trace = write_trace(tmp_path / "trace.json", [call("c1", "bash", {"command": "import json\n# note\n}\nls -la"})])

    report = provenance(trace, tmp_path / "out", skills, prompt)

    assert report["tool_calls"] == []
    assert report["totals"]["authored_lines"] == 0
    assert report["totals"]["copied_share"] is None


def test_invalid_sources_write_nothing(tmp_path: Path) -> None:
    skills, prompt = write_sources(tmp_path)
    trace = mixed_trace(tmp_path / "trace.json")
    for sources in (
        [f"skills={tmp_path / 'missing'}"],
        [f"skills={skills}", f"skills={prompt}"],
        [f"bad name={prompt}"],
        [],
    ):
        result = run(
            PROVENANCE,
            "--trace",
            trace,
            *[part for value in sources for part in ("--source", value)],
            "--out-dir",
            tmp_path / "out",
        )
        assert result.returncode == 1 and json.loads(result.stdout)["error_type"] == "source", result.stdout
    assert not (tmp_path / "out").exists()


def test_aggregate_adds_provenance_without_changing_coverage_and_rejects_changed_evidence(tmp_path: Path) -> None:
    skills, prompt = write_sources(tmp_path)
    trace = mixed_trace(tmp_path / "trace.json")
    audit = bound_audit(tmp_path, "bash")
    out = tmp_path / "measurements"
    measured = run(
        SCRIPTS / "measure.py",
        "--audit",
        audit,
        "--trace",
        trace,
        "--task-id",
        "demo",
        "--evidence-class",
        "agent_under_test",
        "--out-dir",
        out,
    )
    assert measured.returncode == 0, measured.stdout + measured.stderr
    provenance(trace, out, skills, prompt, "--task-id", "demo")

    def aggregate(name: str, *extra: str | Path) -> subprocess.CompletedProcess[str]:
        return run(SCRIPTS / "report.py", "--audit", audit, "--coverage-dir", out, "--out", tmp_path / name, *extra)

    assert aggregate("plain.json").returncode == 0
    with_provenance = aggregate("with.json", "--provenance-dir", out)
    assert with_provenance.returncode == 0, with_provenance.stdout
    assert json.loads(with_provenance.stdout)["provenance_input_count"] == 1
    plain = json.loads((tmp_path / "plain.json").read_text())
    report = json.loads((tmp_path / "with.json").read_text())
    assert "provenance" not in plain and "provenance" not in plain["binding"]
    assert report["provenance"][0]["totals"]["copied_lines"] == 4
    assert report["provenance"][0]["subject"]["run_id"] == "run-1"
    for payload in (plain, report):
        payload.pop("provenance", None)
        payload["binding"].pop("provenance", None)
        payload["binding"].pop("artifacts")
    assert report == plain
    assert run(SCRIPTS / "report.py", "--verify-existing", tmp_path / "with.json").returncode == 0

    other = provenance(write_trace(tmp_path / "other.json", [], trajectory="run-2"), tmp_path / "other", skills, prompt)
    unmeasured = aggregate("bad.json", "--provenance-dir", out, "--provenance-dir", tmp_path / "other")
    assert unmeasured.returncode == 1 and "not an aggregated measurement" in unmeasured.stdout
    assert other["totals"]["tool_call_count"] == 0

    (skills / "demo/SKILL.md").write_text(SKILL_TEXT + "def extra():\n")
    changed = aggregate("changed.json", "--provenance-dir", out)
    assert changed.returncode == 1 and "source 'skills' changed" in changed.stdout
    verified = run(SCRIPTS / "report.py", "--verify-existing", tmp_path / "with.json")
    assert verified.returncode == 1 and "source 'skills' changed" in verified.stdout


def test_rendered_report_shows_provenance_only_when_aggregated(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    content = importlib.import_module("report_content")
    renderer = content.load_module("audit_markdown_renderer", SCRIPTS / "render_report.py")
    shutil.copytree(EXAMPLE / "report-sources", tmp_path / "report-sources")
    shutil.copy2(EXAMPLE / "report-content.json", tmp_path / "report-content.json")
    skills, prompt = write_sources(tmp_path)
    aggregate = bound_gap(tmp_path, tmp_path / "coverage.json", "customer.lookup")
    measurements = tmp_path / "baseline-measurements"
    trace = tmp_path / "baseline.atif.json"
    data = example_data(tmp_path)
    files = [
        tmp_path / "ETHOS.md",
        tmp_path / ".eval-author/audit.md",
        trace,
        aggregate,
        *sorted(measurements.rglob("coverage.json")),
    ]
    kinds = {aggregate: "measurement", files[1]: "audit", trace: "run"}
    added = [
        {"id": f"S{index}", "kind": kinds.get(path, "document"), "path": path.relative_to(tmp_path).as_posix()}
        for index, path in enumerate(files, 10)
    ]
    data["sources"].extend(added)
    data["inputs"].update(audit="S11", measurement="S13")
    data["runs"] = [{"id": "baseline", "task_id": "T1", "source": "S12"}]
    data["tasks"][0]["run_ids"] = ["baseline"]
    names = [row["name"] for row in content.load_audit_spec(files[1])["items"]]
    for mapping, name in zip(data["task_mappings"], names, strict=True):
        mapping["item"] = name
    for row in data["findings"] + data["recommendations"]:
        row["items"] = []

    def render() -> str:
        for source in added:
            source["sha256"] = content.sha((tmp_path / source["path"]).read_bytes())
        save(tmp_path, data)
        return renderer.render(content.load(Path("report-content.json"), tmp_path))[0].decode()

    assert "Code provenance" not in render()

    written = provenance(trace, measurements, skills, prompt, "--task-id", "baseline")
    result = run(
        SCRIPTS / "report.py",
        "--audit",
        files[1],
        "--coverage-dir",
        measurements,
        "--provenance-dir",
        measurements,
        "--out",
        aggregate,
    )
    assert result.returncode == 0, result.stdout
    path = next(measurements.rglob("provenance.json")).relative_to(tmp_path).as_posix()
    added.append({"id": "S19", "kind": "document", "path": path})
    data["sources"].append(added[-1])
    report = render()
    assert written["totals"]["authored_lines"] == 0
    section = report.split("#### Code provenance\n\n", 1)[1].split("\n\n", 2)[1]
    assert section.splitlines()[2] == "| baseline | 0 | 0 | No authored code | prompt: 0, skills: 0 |"
