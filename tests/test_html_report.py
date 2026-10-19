# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Offline suite reporting with synthetic native evidence; never run evaluations."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"
_spec = importlib.util.spec_from_file_location("eval_html_report", SCRIPTS / "report.py")
assert _spec and _spec.loader
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, dict) else value, encoding="utf-8")
    return path


def task(root, name="refund", readme=None):
    path = f".eval-author/tasks/{name}"
    write(root, path + "/task.toml", f'[task]\nname = "org/{name}"\n')
    write(root, path + "/instruction.md", "SYNTHETIC_REQUEST")
    if readme is not None:
        write(root, path + "/README.md", readme)
    return path


def trial(root, path, *, reward=1, error=None, finished=True, agent="test-agent", trial_id=None):
    data = {
        "id": trial_id or path,
        "task_name": "org/refund",
        "task_id": {"path": ".eval-author/tasks/refund"},
        "task_checksum": "synthetic-checksum",
        "agent_info": {"name": agent, "model_info": {"name": "synthetic"}},
        "exception_info": error,
        "verifier_result": {"rewards": {"reward": reward}},
        "started_at": "2026-10-01T10:00:00+00:00",
        "finished_at": "2026-10-01T10:01:00+00:00" if finished else None,
    }
    write(root, path + "/result.json", data)
    return data


def job(root, path=".eval-author/jobs/baseline", total=2):
    write(
        root,
        path + "/result.json",
        {
            "id": "job-id",
            "n_total_trials": total,
            "stats": {},
            "started_at": "2026-10-01T10:00:00+00:00",
            "finished_at": "2026-10-01T10:01:00+00:00",
        },
    )
    return path


def rule(path):
    return {
        "runs": {path: {"role": "agent", "reward_key": "reward", "pass_value": 1, "reward_min": 0, "reward_max": 1}}
    }


def test_bootstrap_and_documentation_are_deterministic(tmp_path):
    path = task(
        tmp_path,
        readme="# Refund\n## Behavior under test\nRefund authorization.\n## Expected result\nOnly approved refunds are stored.\n",
    )
    first = report.refresh(tmp_path, patch={"tasks": {path: {"display_id": "R1"}}})
    assert first["tasks"][0]["behavior"] == "Refund authorization."
    assert first["tasks"][0]["expected_outcome"] == "Only approved refunds are stored."
    assert first["tasks"][0]["name"] == "org/refund"
    assert first["runs"] == []
    output = tmp_path / ".eval-author/report.html"
    before = output.read_bytes()
    modified = output.stat().st_mtime_ns
    assert report.refresh(tmp_path) == first
    assert output.read_bytes() == before
    assert output.stat().st_mtime_ns == modified
    assert sorted(p.name for p in output.parent.iterdir()) == ["README.md", "report.html", "tasks"]
    write(
        tmp_path,
        path + "/README.md",
        "## Behavior under test\nUpdated behavior.\n## Expected result\nUpdated result.\n",
    )
    updated = report.refresh(tmp_path)
    assert updated["tasks"][0]["behavior"] == "Updated behavior."
    assert updated["tasks"][0]["display_id"] == "R1"
    assert "product_area" not in updated["tasks"][0]
    assert updated["tasks"][0]["readme_revision"] != first["tasks"][0]["readme_revision"]


@pytest.mark.parametrize(
    "body,expected",
    [
        ("# Title\n", None),
        ("## Behavior under test\n\n## Expected result\nSuccess", None),
        ("## Behavior under test\nA\n## Behavior under test\nB", None),
        ("```md\n## Behavior under test\nFake\n```\n## Behavior under test\nReal", "Real"),
        ("~~~md\n## Behavior under test\nFake\n~~~\n## Behavior under test\nReal", "Real"),
        ("## Behavior under test\nOne paragraph.\n# Stop\nIgnored", "One paragraph."),
        ("## Behavior under test\n" + "x" * 2001, None),
    ],
)
def test_readme_section_boundaries(body, expected):
    assert report.readme_sections(body).get("Behavior under test") == expected


def test_import_fields_and_canonical_readme(tmp_path):
    path = task(tmp_path, readme="# Existing documentation\nOther sections.")
    metadata = {
        "tasks": {
            path: {
                "behavior": "Documented import.",
                "expected_outcome": "Known result.",
                "source_refs": [path + "/README.md"],
            }
        }
    }
    assert report.refresh(tmp_path, patch=metadata)["tasks"][0]["behavior"] == "Documented import."
    write(tmp_path, path + "/README.md", "## Behavior under test\nCanonical.\n## Expected result\nCanonical result.")
    assert report.refresh(tmp_path)["tasks"][0]["behavior"] == "Canonical."


def test_task_paths_keep_same_named_parent_directories_distinct(tmp_path):
    original = task(tmp_path)
    paths = [original]
    for suite in ["import", "chat"]:
        path = f".eval-author/suites/{suite}/tasks/refund"
        shutil.copytree(tmp_path / original, tmp_path / path)
        paths.append(path)
    first = report.refresh(tmp_path)
    assert [t["path"] for t in first["tasks"]] == sorted(paths)
    assert len({report.task_sort(t)[0] for t in first["tasks"]}) == 3
    # Search scope and overlapping roots do not define sections or duplicate rows.
    second = report.refresh(tmp_path, task_roots=[".", ".eval-author/suites/chat/tasks"])
    assert second["tasks"] == first["tasks"]
    assert all("product_area" not in t for t in second["tasks"])


@pytest.mark.parametrize("remove_task", [False, True])
def test_old_product_labels_are_removed_without_losing_metadata(tmp_path, remove_task):
    path = task(tmp_path)
    old = report.refresh(tmp_path, patch={"tasks": {path: {"display_id": "R1"}}})
    old["tasks"][0]["product_area"] = "Invented area"
    old["metadata"]["tasks"][path]["product_area"] = "Invented area"
    template = (SCRIPTS / "report-template.html").read_text()
    output = tmp_path / ".eval-author/report.html"
    output.write_text(template.replace(report.MARKER, json.dumps(old)))
    if remove_task:
        shutil.rmtree(tmp_path / path)
    updated = report.refresh(tmp_path)
    assert updated["metadata"]["tasks"][path] == {"display_id": "R1"}
    assert updated["tasks"][0]["path"] == path
    assert updated["tasks"][0]["display_id"] == "R1"
    assert "product_area" not in updated["tasks"][0]
    assert updated["tasks"][0]["status"] == ("unverified" if remove_task else "found")
    assert "Invented area" not in output.read_text()
    with pytest.raises(report.ReportError):
        report.refresh(tmp_path, patch={"tasks": {path: {"product_area": "New area"}}})


def test_runs_group_attempts_without_double_counting(tmp_path):
    path = job(tmp_path)
    trial(tmp_path, path + "/one")
    trial(tmp_path, path + "/two", reward=0)
    standalone = ".eval-author/jobs/checks/single"
    trial(tmp_path, standalone, reward=0)
    metadata = rule(path)
    metadata["runs"].update(rule(standalone)["runs"])
    result = report.refresh(tmp_path, patch=metadata)
    assert len(result["runs"]) == 1
    runs = {r["path"]: r for r in result["runs"]}
    assert runs[path]["score_label"] == "1 / 2 reward (50.0%) · 2/2 scored"
    assert len(runs[path]["attempts"]) == 2
    assert runs[path]["route"] == "jobs/baseline"
    legacy = result["standalone_trials"][0]
    assert legacy["path"] == standalone
    assert legacy["score_label"] == "0 / 1 reward (0.0%) · 1/1 scored"
    assert legacy["route"].endswith("/org%2Frefund/trials/single")
    assert all(run["kind"] == "job" for run in result["runs"])


def test_missing_nonbinary_errors_and_invalid_are_not_passes(tmp_path):
    path = job(tmp_path, total=5)
    trial(tmp_path, path + "/pass")
    trial(tmp_path, path + "/fail", reward=0)
    trial(tmp_path, path + "/error", error={"exception_type": "Timeout"})
    trial(tmp_path, path + "/invalid", trial_id="invalid-1")
    metadata = rule(path)
    metadata["runs"][path]["invalid_attempts"] = {"invalid-1": "Synthetic invalid grader evidence."}
    run = report.refresh(tmp_path, patch=metadata)["runs"][0]
    assert (run["passed"], run["failed"], run["errors"], run["invalid"], run["unknown"]) == (1, 1, 1, 1, 1)
    assert run["status"] == "incomplete"
    assert run["attempts"][-1]["rewards"]  # Native rewards retained.


def test_unknown_pass_rule_preserves_native_reward(tmp_path):
    path = ".eval-author/jobs/checks/graded"
    trial(tmp_path, path, reward=0.65)
    run = report.refresh(tmp_path)["standalone_trials"][0]
    assert run["passed"] is None
    assert "0.65" in run["score_label"]
    assert run["attempts"][0]["rewards"]["reward"] == 0.65


def test_control_role_and_config_only_run(tmp_path):
    trial(tmp_path, ".eval-author/jobs/controls/nop", agent="nop", reward=0)
    write(tmp_path, ".eval-author/jobs/pending/config.json", {"job_name": "pending", "agents": []})
    result = report.refresh(tmp_path)
    runs = {r["name"]: r for r in result["runs"]}
    assert result["standalone_trials"][0]["role"] == "control"
    assert runs["pending"]["status"] == "unknown"
    assert runs["pending"]["total"] is None


def test_malformed_native_result_visible_without_fabricated_score(tmp_path):
    write(tmp_path, ".eval-author/jobs/bad/trial/result.json", "{broken")
    run = report.refresh(tmp_path)["standalone_trials"][0]
    assert run["status"] == "unknown"
    assert run["passed"] is None
    assert run["issues"]


def test_duplicate_trial_identity_disables_score(tmp_path):
    path = job(tmp_path)
    trial(tmp_path, path + "/one", trial_id="duplicate")
    trial(tmp_path, path + "/two", trial_id="duplicate")
    run = report.refresh(tmp_path, patch=rule(path))["runs"][0]
    assert run["passed"] is None
    assert run["score_label"].startswith("Score unavailable (duplicate attempt IDs)")
    assert run["reward_sum"] is None
    assert any("Duplicate" in issue for issue in run["issues"])


def test_job_rollup_preserves_fractional_credit_and_counts_trials(tmp_path):
    path = job(tmp_path, total=3)
    for name, reward in [("one", 1), ("two", 0.65), ("three", 0)]:
        trial(tmp_path, path + "/" + name, reward=reward)
    run = report.refresh(tmp_path, patch=rule(path))["runs"][0]
    assert run["reward_sum"] == pytest.approx(1.65)
    assert (run["score_min"], run["score_max"], run["scored"], run["completed"]) == (0, 3, 3, 3)
    assert (run["passed"], run["failed"]) == (1, 2)
    assert run["score_label"] == "1.65 / 3 reward (55.0%) · 3/3 scored"


def test_named_reward_without_pass_rule_and_explicit_scale(tmp_path):
    path = job(tmp_path)
    for name, quality in [("one", 3), ("two", 5)]:
        data = trial(tmp_path, path + "/" + name)
        data["verifier_result"]["rewards"] = {"quality": quality, "reward": 0}
        write(tmp_path, path + "/" + name + "/result.json", data)
    patch = {"runs": {path: {"reward_key": "quality", "reward_min": 0, "reward_max": 5}}}
    run = report.refresh(tmp_path, patch=patch)["runs"][0]
    assert run["reward_sum"] == 8
    assert run["passed"] is None
    assert run["score_label"] == "8 / 10 quality (80.0%) · 2/2 scored"


@pytest.mark.parametrize("bounds", [None, (0, 1), (-1, 1)])
def test_graph_needs_a_documented_range_and_preserves_out_of_range_reward(tmp_path, bounds):
    path = job(tmp_path, total=1)
    trial(tmp_path, path + "/one", reward=2)
    fields = {"reward_key": "reward"}
    if bounds:
        fields.update(reward_min=bounds[0], reward_max=bounds[1])
    run = report.refresh(tmp_path, patch={"runs": {path: fields}})["runs"][0]
    assert run["reward_sum"] == 2
    assert run["score_min"] is None and run["score_max"] is None
    assert "2 total" in run["score_label"]


def test_nonzero_reward_minimum_sets_the_meter_range(tmp_path):
    path = job(tmp_path)
    trial(tmp_path, path + "/one", reward=-1)
    trial(tmp_path, path + "/two", reward=0)
    patch = {"runs": {path: {"reward_min": -1, "reward_max": 1}}}
    run = report.refresh(tmp_path, patch=patch)["runs"][0]
    assert (run["reward_sum"], run["score_min"], run["score_max"]) == (-1, -2, 2)
    assert "25.0%" in run["score_label"]


def test_partial_job_separates_reward_from_completion_and_missing_data(tmp_path):
    path = job(tmp_path, total=6)
    trial(tmp_path, path + "/scored", reward=0.65)
    trial(tmp_path, path + "/missing", reward=None)
    trial(tmp_path, path + "/pending", finished=False)
    trial(tmp_path, path + "/error", error={"exception_type": "Timeout"})
    trial(tmp_path, path + "/invalid", trial_id="invalid")
    patch = rule(path)
    patch["runs"][path]["invalid_attempts"] = {"invalid": "Known verifier defect."}
    run = report.refresh(tmp_path, patch=patch)["runs"][0]
    assert run["status"] == "incomplete"
    assert (run["reward_sum"], run["score_max"], run["scored"]) == (0.65, 1, 1)
    assert (run["errors"], run["invalid"], run["unknown"]) == (1, 1, 3)
    assert "1/6 scored; partial" in run["score_label"]
    assert "65.0%" in run["score_label"]


def test_no_scored_trials_is_unavailable_even_with_a_pass_rule(tmp_path):
    path = job(tmp_path, total=1)
    trial(tmp_path, path + "/pending", finished=False)
    run = report.refresh(tmp_path, patch=rule(path))["runs"][0]
    assert run["reward_sum"] is None and run["passed"] is None
    assert run["score_max"] is None
    assert run["score_label"] == "Score unavailable · 0/1 scored; partial"


def test_unfinished_job_is_partial_even_when_all_trial_scores_exist(tmp_path):
    path = job(tmp_path, total=1)
    native = tmp_path / path / "result.json"
    result = json.loads(native.read_text())
    result["finished_at"] = None
    write(tmp_path, path + "/result.json", result)
    trial(tmp_path, path + "/one")
    run = report.refresh(tmp_path, patch=rule(path))["runs"][0]
    assert run["status"] == "incomplete"
    assert run["reward_sum"] == 1
    assert "1/1 scored; partial" in run["score_label"]


def test_retry_or_conflicting_total_cannot_inflate_reward(tmp_path):
    path = job(tmp_path, total=1)
    trial(tmp_path, path + "/first")
    trial(tmp_path, path + "/retry")
    run = report.refresh(tmp_path, patch=rule(path))["runs"][0]
    assert len(run["attempts"]) == 2
    assert run["reward_sum"] is None and run["score_max"] is None
    assert run["passed"] is None
    assert "conflicting trial total or retries" in run["score_label"]


def test_job_identity_uses_disk_name_and_full_path_with_overlapping_roots(tmp_path):
    paths = [job(tmp_path, f".eval-author/jobs/{suite}/same", total=1) for suite in ["a", "b"]]
    for path in paths:
        trial(tmp_path, path + "/one")
        write(tmp_path, path + "/config.json", {"job_name": "different-config-name"})
    data = report.refresh(tmp_path, jobs_roots=[".eval-author/jobs", ".eval-author/jobs/a"])
    assert {run["path"] for run in data["runs"]} == set(paths)
    assert all(run["name"] == "same" and run["reward_sum"] == 1 for run in data["runs"])
    assert all("on-disk" in " ".join(run["issues"]) for run in data["runs"])
    assert data["standalone_trials"] == []


def save_v1_report(root, data):
    data["schema_version"] = 1
    data["runs"].extend(data.pop("standalone_trials"))
    for run in data["runs"]:
        for key in report.SCORE_FIELDS:
            del run[key]
    write(
        root,
        ".eval-author/report.html",
        '<script id="report-data" type="application/json">' + json.dumps(data) + "</script>",
    )


def test_v1_migration_keeps_missing_trials_in_legacy_and_preserves_metadata(tmp_path):
    path = job(tmp_path, total=1)
    trial(tmp_path, path + "/one")
    standalone = ".eval-author/jobs/checks/single"
    trial(tmp_path, standalone)
    patch = rule(path)
    patch["runs"].update(rule(standalone)["runs"])
    data = report.refresh(tmp_path, patch=patch)
    save_v1_report(tmp_path, data)
    shutil.rmtree(tmp_path / standalone)
    updated = report.refresh(tmp_path)
    assert updated["schema_version"] == 2
    assert [run["path"] for run in updated["runs"]] == [path]
    assert updated["standalone_trials"][0]["status"] == "unverified"
    assert updated["metadata"] == patch | {"tasks": {}, "task_roots": ["."], "jobs_roots": [".eval-author/jobs"]}
    assert report.refresh(tmp_path) == updated


def test_new_job_owns_previously_standalone_trial_without_ghost_rows(tmp_path):
    parent = ".eval-author/jobs/becomes-job"
    trial(tmp_path, parent + "/one")
    save_v1_report(tmp_path, report.refresh(tmp_path))
    job(tmp_path, parent, total=1)
    updated = report.refresh(tmp_path)
    assert len(updated["runs"]) == 1
    assert len(updated["runs"][0]["attempts"]) == 1
    assert updated["standalone_trials"] == []


def test_job_replacing_standalone_at_same_path_has_no_legacy_duplicate(tmp_path):
    path = ".eval-author/jobs/reclassified"
    trial(tmp_path, path)
    report.refresh(tmp_path)
    job(tmp_path, path, total=1)
    trial(tmp_path, path + "/one")
    updated = report.refresh(tmp_path)
    assert [run["path"] for run in updated["runs"]] == [path]
    assert updated["standalone_trials"] == []


def test_browser_renders_job_reward_meter_and_separate_legacy_trials(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise report rendering")
    path = job(tmp_path, total=3)
    for name, reward in [("one", 1), ("two", 0.65), ("three", 0)]:
        trial(tmp_path, path + "/" + name, reward=reward)
    trial(tmp_path, ".eval-author/jobs/checks/pending", finished=False)
    data = report.refresh(tmp_path, patch=rule(path))
    source = (SCRIPTS / "report-template.html").read_text()
    helper = source[source.index("function runRow(") : source.index("byId('search').addEventListener")]
    program = r"""
const assert=require('node:assert/strict');
const vm=require('node:vm');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
class Element{
  constructor(tag,text,className){this.tag=tag;this.textContent=text||'';this.className=className;this.children=[];this.style={};this.dataset={};this.attrs={};}
  append(...items){this.children.push(...items);}
  replaceChildren(){this.children=[];}
  setAttribute(key,value){this.attrs[key]=value;}
}
const ids={};
const byId=id=>ids[id]||(ids[id]=new Element('div'));
const context={data:input.data,byId,element:(...args)=>new Element(...args),viewerAction:label=>new Element('button',label)};
vm.createContext(context);vm.runInContext(input.helper+'\ndrawRuns();',context);
const descendants=node=>[node,...node.children.flatMap(descendants)];
assert.equal(ids['run-rows'].children.length,1);
assert.equal(ids['legacy-rows'].children.length,1);
assert.equal(ids['legacy-trials'].hidden,false);
assert.equal(ids['legacy-summary'].textContent,'Legacy trials (1)');
const job=ids['run-rows'].children[0];
assert.equal(job.dataset.kind,'job');
const nodes=descendants(job);
assert(nodes.some(n=>n.className==='run-path'&&n.textContent===input.data.runs[0].path));
const meter=nodes.find(n=>n.attrs.role==='meter');
assert.equal(meter.attrs['aria-valuenow'],'1.65');
assert.equal(meter.attrs['aria-valuemax'],'3');
assert.equal(nodes.find(n=>n.className==='score-fill').style.width,'55%');
assert(!descendants(ids['legacy-rows']).some(n=>n.attrs.role==='meter'));
input.data.runs[0].status='unverified';
vm.runInContext('drawRuns();',context);
assert(!descendants(ids['run-rows']).some(n=>n.attrs.role==='meter'));
assert(descendants(ids['run-rows']).some(n=>n.textContent==='Inventory unverified'));
input.data.runs=[];input.data.standalone_trials=[];
vm.runInContext('drawRuns();',context);
assert.equal(ids['no-runs'].hidden,false);
assert.equal(ids['legacy-trials'].hidden,true);
"""
    result = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"helper": helper, "data": data}),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_markdown_legacy_trials_mirrors_the_separate_html_section(tmp_path):
    path = job(tmp_path, total=1)
    trial(tmp_path, path + "/one")
    legacy = ".eval-author/jobs/checks/single"
    trial(tmp_path, legacy)
    report.refresh(tmp_path, patch=rule(path))
    raw = (tmp_path / ".eval-author/README.md").read_text()
    jobs, standalone = raw.split("### Legacy trials")
    assert "| Job | Reward |" in jobs and "| Trial | Reward |" in standalone
    assert legacy not in jobs and path not in standalone
    assert legacy in standalone and path in jobs


def test_incomplete_search_preserves_old_rows(tmp_path):
    task(tmp_path)
    report.refresh(tmp_path)
    data = report.refresh(tmp_path, max_entries=1)
    assert not data["inventory"]["complete"]
    assert data["tasks"][0]["status"] == "unverified"
    assert data["warnings"]


def test_scanner_is_used_without_modification(tmp_path, monkeypatch):
    path = task(tmp_path)
    calls = []
    scanner = report.search_tasks

    def observe(*args, **kwargs):
        calls.append((args, kwargs))
        return scanner(*args, **kwargs)

    monkeypatch.setattr(report, "search_tasks", observe)
    data = report.refresh(tmp_path, task_roots=[path])
    assert calls and calls[0][1]["provider"] == "harbor"
    assert data["tasks"][0]["path"] == path


def test_nested_checkout_exclusion_and_explicit_selection(tmp_path):
    task(tmp_path)
    nested = tmp_path / "nested"
    task(nested, "nested-refund")
    write(nested, ".git", "gitdir: elsewhere")
    result = report.refresh(tmp_path)
    assert len(result["tasks"]) == 1
    assert result["inventory"]["excluded"] == ["nested"]
    assert len(report.refresh(tmp_path, task_roots=[".", "nested"])["tasks"]) == 2


def test_no_symlink_following(tmp_path):
    path = task(tmp_path)
    alias = tmp_path / ".eval-author/alias"
    try:
        alias.symlink_to(tmp_path / path, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    assert len(report.refresh(tmp_path)["tasks"]) == 1
    with pytest.raises(report.ReportError, match="Symlink"):
        report.refresh(tmp_path, task_roots=[".eval-author/alias"])


@pytest.mark.parametrize(
    "patch",
    [
        {"summary": "Unrequested narrative"},
        {"tasks": {"../escape": {}}},
        {"runs": {".eval-author/jobs/a": {"pass_value": 1}}},
        {"runs": {".eval-author/jobs/a": {"reward_min": 0}}},
        {"runs": {".eval-author/jobs/a": {"reward_min": 1, "reward_max": 1}}},
        {"runs": {".eval-author/jobs/a": {"reward_min": 5, "reward_max": 0}}},
        {"tasks": {".eval-author/tasks/a": {"behavior": "Unsourced"}}},
    ],
)
def test_bad_metadata_preserves_report(tmp_path, patch):
    report.refresh(tmp_path)
    output = tmp_path / ".eval-author/report.html"
    before = output.read_bytes()
    readme_before = (tmp_path / ".eval-author/README.md").read_bytes()
    with pytest.raises(report.ReportError):
        report.refresh(tmp_path, patch=patch)
    assert output.read_bytes() == before
    assert (tmp_path / ".eval-author/README.md").read_bytes() == readme_before


def test_html_escape_and_embedded_payload(tmp_path):
    path = task(
        tmp_path,
        readme="## Behavior under test\n</script><img src=x onerror=alert(1)>\n## Expected result\n<script>bad()</script>",
    )
    report.refresh(tmp_path, patch={"tasks": {path: {"display_id": '<img onerror="bad()">'}}})
    output = tmp_path / ".eval-author/report.html"
    raw = output.read_text()
    assert "</script><img src=x" not in raw
    data, _ = report.previous_report(output)
    assert "<img" in data["tasks"][0]["behavior"]
    assert ".innerHTML" not in raw
    preview = data["tasks"][0]["readme_html"]
    assert "<script>" not in preview
    assert "<img" not in preview
    assert "&lt;script&gt;" in preview


def test_readme_preview_preserves_markdown_and_refreshes(tmp_path):
    path = task(
        tmp_path,
        readme=(
            "# Task\n\n> Quoted input\n\n**Expected** `state`\n\n"
            "| Check | Result |\n| --- | --- |\n| Saved | Yes |\n\n"
            '```json\n{"reward": 1}\n```\n\n'
            "[Verifier](tests/verify.py)\n\n[Unsafe](javascript:alert(1))\n"
        ),
    )
    first = report.refresh(tmp_path)
    preview = first["tasks"][0]["readme_html"]
    for fragment in ("<h1>Task</h1>", "<blockquote>", "<strong>Expected</strong>", "<table>", 'class="language-json"'):
        assert fragment in preview
    assert 'href="tests/verify.py"' in preview
    assert 'href="javascript:' not in preview
    readme = tmp_path / path / "README.md"
    readme.write_text("# Updated\n", encoding="utf-8")
    assert report.refresh(tmp_path)["tasks"][0]["readme_html"] == "<h1>Updated</h1>\n"
    readme.unlink()
    assert report.refresh(tmp_path)["tasks"][0]["readme_html"] is None


def test_readme_preview_is_bounded(tmp_path):
    task(tmp_path, readme="x" * (report.MAX_README_BYTES + 1))
    data = report.refresh(tmp_path)
    assert data["tasks"][0]["readme_html"] is None
    assert "Task README could not be read." in data["tasks"][0]["issues"]


def test_legacy_readme_is_previewed_without_authoring_missing_descriptions(tmp_path):
    source = "# Existing task\n\n## Grading\nThe verifier checks saved state.\n"
    path = task(tmp_path, readme=source)
    item = report.refresh(tmp_path)["tasks"][0]
    assert item["behavior"] is None
    assert item["expected_outcome"] is None
    assert item["grading_rule"] is None
    assert "The verifier checks saved state." in item["readme_html"]
    assert (tmp_path / path / "README.md").read_text() == source


def test_report_before_readme_previews_can_be_refreshed(tmp_path):
    task(tmp_path, readme="# Existing task\n")
    old = report.refresh(tmp_path)
    for item in old["tasks"]:
        item.pop("readme_html")
    write(
        tmp_path,
        ".eval-author/report.html",
        '<script id="report-data" type="application/json">' + json.dumps(old) + "</script>",
    )
    updated = report.refresh(tmp_path)
    assert updated["tasks"][0]["readme_html"] == "<h1>Existing task</h1>\n"


def test_corrupt_existing_report_and_lock_preserve_output(tmp_path):
    output = write(tmp_path, ".eval-author/report.html", "<html>not a generated report</html>")
    with pytest.raises(report.ReportError):
        report.refresh(tmp_path)
    assert output.read_text() == "<html>not a generated report</html>"
    write(tmp_path, ".eval-author/.report.lock", "")
    with pytest.raises(report.ReportError, match="already running"):
        report.refresh(tmp_path)


def test_copied_script_and_template_installation(tmp_path):
    installed = tmp_path / "copied-skill/scripts"
    installed.mkdir(parents=True)
    for name in ["report.py", "report-template.html", "search_tasks.py"]:
        shutil.copy2(SCRIPTS / name, installed / name)
    project = tmp_path / "project"
    project.mkdir()
    task(project)
    result = subprocess.run(
        [sys.executable, "-I", str(installed / "report.py"), "refresh", "--repo", str(project)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["tasks"] == 1
    assert (project / ".eval-author/report.html").is_file()
    assert json.loads(result.stdout)["readme"] == ".eval-author/README.md"
    assert (project / ".eval-author/README.md").is_file()


def test_runtime_paths_are_portable(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    task(original)
    before = report.refresh(original)
    relocated = tmp_path / "relocated"
    shutil.copytree(original, relocated)
    assert report.refresh(relocated) == before


def test_native_harbor_viewer_routes_match_pinned_manifest():
    harbor = pytest.importorskip("harbor")
    static = Path(harbor.__file__).parent / "viewer/static/assets"
    manifests = list(static.glob("manifest-*.js"))
    if not manifests:
        pytest.skip("Native Harbor frontend assets unavailable")
    source = manifests[0].read_text()
    assert '"path":"jobs/:jobName"' in source
    assert '"path":"task-definitions/:taskName"' in source
    assert "tasks/:source/:agent/:modelProvider/:modelName/:taskName/trials/:trialName" in source


def test_removed_evidence_is_retained_but_explicit_scope_change_is_respected(tmp_path):
    task_path = task(tmp_path)
    run_path = ".eval-author/jobs/checks/one"
    trial(tmp_path, run_path)
    report.refresh(tmp_path, patch=rule(run_path))
    shutil.rmtree(tmp_path / run_path)
    shutil.rmtree(tmp_path / task_path)
    data = report.refresh(tmp_path)
    assert data["runs"] == []
    assert data["standalone_trials"][0]["status"] == "unverified"
    assert data["tasks"][0]["status"] == "unverified"
    other = tmp_path / "other"
    other.mkdir()
    data = report.refresh(tmp_path, task_roots=["other"], jobs_roots=["other"])
    assert data["tasks"] == []
    assert data["runs"] == []
    assert data["standalone_trials"] == []


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("task", "ok"),
        ("job", "ok"),
        ("trial", "ok"),
        ("missing", "missing"),
        ("wrong-mode", "mode"),
        ("different-task", "different"),
        ("different-job", "different"),
        ("non-harbor", "response"),
        ("server-error", "response"),
        ("blocked", "unreachable"),
        ("timeout", "unreachable"),
    ],
)
def test_viewer_checks_mode_item_and_identity_before_navigation(scenario, expected):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise the browser's viewer check")
    source = (SCRIPTS / "report-template.html").read_text()
    helper = source[source.index("function viewerFailure(") : source.index("let retryViewer=null;")]
    program = r"""
const assert=require('node:assert/strict');
const vm=require('node:vm');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const scenario=input.scenario;
const jobs=['job','trial','different-job'].includes(scenario);
const mode=jobs?'jobs':'tasks';
const record=jobs?{id:'native-id',path:'.eval-author/jobs/example'}:{path:'.eval-author/tasks/example'};
const route=jobs?'jobs/example'+(scenario==='trial'?'/tasks/_/agent/_/model/task/trials/attempt':''):'task-definitions/example';
const calls=[];
const context={AbortController,clearTimeout,setTimeout:(fn,ms)=>{
  assert.equal(ms,3000);return setTimeout(fn,scenario==='timeout'?10:ms);
},fetch:async(url,options)=>{
  calls.push(url);assert.equal(options.credentials,'omit');assert.equal(options.mode,'cors');
  assert.equal(options.cache,'no-store');
  if(scenario==='blocked')throw new TypeError('Browser blocked request');
  if(scenario==='timeout')return new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('aborted'))));
  const config=url.endsWith('/api/config');
  const status=scenario==='server-error'?500:(!config&&scenario==='missing'?404:200);
  return {ok:status===200,status,json:async()=>{
    if(scenario==='non-harbor')throw new SyntaxError('HTML response');
    if(config)return {mode:scenario==='wrong-mode'?'jobs':mode,folder:'/project/.eval-author'};
    if(jobs)return {id:scenario==='different-job'?'other-id':'native-id'};
    return {name:'example',task_dir:scenario==='different-task'?'/another/tasks/example':'/project/.eval-author/tasks/example'};
  }};
}};
vm.createContext(context);vm.runInContext(input.helper,context);
(async()=>{
  let actual='ok';try{await context.checkViewer('http://localhost:8082',mode,route,record);}catch(error){actual=error.kind;}
  assert.equal(actual,input.expected);
  assert.equal(calls[0],'http://localhost:8082/api/config');
  if(scenario==='trial')assert.equal(calls[1],'http://localhost:8082/api/jobs/example/trials/attempt');
  if(['blocked','timeout','wrong-mode','non-harbor','server-error'].includes(scenario))assert.equal(calls.length,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"helper": helper, "scenario": scenario, "expected": expected}),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def markdown_tokens(tmp_path):
    from markdown_it import MarkdownIt

    return MarkdownIt("commonmark").enable("table").parse((tmp_path / ".eval-author/README.md").read_text())


def test_markdown_mirrors_report_tabs_rows_and_links(tmp_path):
    path = task(
        tmp_path,
        readme="## Behavior under test\nRefund authorization.\n## Expected result\nOnly approved refunds are stored.\n",
    )
    other = "evals/other suite/tasks/with # fragment"
    shutil.copytree(tmp_path / path, tmp_path / other)
    run_path = job(tmp_path)
    trial(tmp_path, run_path + "/pass")
    trial(tmp_path, run_path + "/fail", reward=0)
    patch = rule(run_path)
    patch["tasks"] = {path: {"grading_kind": "deterministic", "grading_rule": "All state checks pass."}}
    data = report.refresh(tmp_path, patch=patch)
    tokens = markdown_tokens(tmp_path)
    headings = [(token.tag, tokens[i + 1].content) for i, token in enumerate(tokens) if token.type == "heading_open"]
    assert [(tag, text) for tag, text in headings if tag == "h2"] == [("h2", "Tasks"), ("h2", "Runs")]
    assert len([token for token in tokens if token.type == "table_open"]) == 3
    assert ("h3", "tasks") in headings
    assert ("h3", "evals/other suite/tasks") in headings
    raw = (tmp_path / ".eval-author/README.md").read_text()
    assert "Settings" not in raw
    assert "http://127.0.0.1:8081/task-definitions/with%20%23%20fragment" in raw
    assert "[Task README](../evals/other%20suite/tasks/with%20%23%20fragment/README.md)" in raw
    assert "[Task README](tasks/refund/README.md)" in raw
    assert "http://127.0.0.1:8080/jobs/baseline" in raw
    # Compare rendered text to the HTML report's source records, not independently authored content.
    text = " ".join(child.content for token in tokens for child in token.children or [] if child.type == "text")
    for item in data["tasks"]:
        for field in ("name", "behavior", "expected_outcome"):
            assert item[field] in text
    assert "deterministic · All state checks pass." in text
    assert "Verifier not documented · Grading not documented" in text
    assert "1/2 passed" in text
    assert "agent · completed" in text
    assert all(part in text for part in data["runs"][0]["date"].split("T"))
    template = (SCRIPTS / "report-template.html").read_text()
    html_tabs = re.findall(r'<button role="tab"[^>]*>([^<]+)</button>', template)
    assert [text for tag, text in headings if tag == "h2"] == [name for name in html_tabs if name != "Settings"]
    assert "<h1>NeMo Eval Author Report</h1>" in template
    assert headings[0] == ("h1", "NeMo Eval Author Report")
    assert "jobsURL:'http://127.0.0.1:8080',tasksURL:'http://127.0.0.1:8081'" in template


def test_markdown_refresh_repairs_missing_or_edited_mirror_without_touching_html(tmp_path):
    path = task(tmp_path, readme="## Behavior under test\nOriginal.\n## Expected result\nSaved.")
    plan = write(tmp_path, ".eval-author/first-eval.md", "Existing planning and run summary.")
    report.refresh(tmp_path)
    html_path = tmp_path / ".eval-author/report.html"
    md_path = tmp_path / ".eval-author/README.md"
    expected = md_path.read_bytes()
    stamp = md_path.stat().st_mtime_ns
    html_stamp = html_path.stat().st_mtime_ns
    report.refresh(tmp_path)
    assert md_path.stat().st_mtime_ns == stamp
    for edit in (None, "A hand-edited generated README"):
        if edit is None:
            md_path.unlink()
        else:
            md_path.write_text(edit)
        report.refresh(tmp_path)
        assert md_path.read_bytes() == expected
        assert html_path.stat().st_mtime_ns == html_stamp
    write(tmp_path, path + "/README.md", "## Behavior under test\nUpdated.\n## Expected result\nSaved.")
    report.refresh(tmp_path)
    assert "Updated" in md_path.read_text()
    assert "Original" not in md_path.read_text()
    assert plan.read_text() == "Existing planning and run summary."


def test_markdown_text_cannot_inject_tables_headings_links_or_html(tmp_path):
    dangerous = (
        "| row\n## forged\n<script>alert(1)</script> [bad](javascript:alert(1)) ![remote](https://example.test/pixel)"
    )
    path = task(tmp_path)
    report.refresh(tmp_path, patch={"tasks": {path: {"grading_rule": dangerous}}})
    tokens = markdown_tokens(tmp_path)
    assert len([token for token in tokens if token.type == "tr_open"]) == 2  # Header and one task.
    assert not any(
        token.type == "heading_open" and token.tag == "h2" and tokens[i + 1].content == "forged"
        for i, token in enumerate(tokens)
    )
    children = [child for token in tokens for child in token.children or []]
    assert not any(child.type == "image" for child in children)
    assert not any(child.type == "link_open" and child.attrGet("href").startswith("javascript:") for child in children)
    assert not any(child.type == "html_inline" and "script" in child.content for child in children)
    assert "<script>alert(1)</script>" in "".join(child.content for child in children if child.type == "text")


def test_markdown_preserves_warnings_missing_data_and_stale_status(tmp_path):
    path = task(tmp_path, readme="# Existing task")
    run_path = job(tmp_path, total=1)
    trial(tmp_path, run_path + "/one", reward=0.65)
    report.refresh(tmp_path)
    raw = (tmp_path / ".eval-author/README.md").read_text()
    assert "Not documented" in raw
    assert "reward: 0.65 total" in raw
    shutil.rmtree(tmp_path / path)
    shutil.rmtree(tmp_path / run_path)
    data = report.refresh(tmp_path, max_entries=1)
    raw = (tmp_path / ".eval-author/README.md").read_text()
    for warning in data["warnings"]:
        assert report.markdown_text(warning) in raw
    assert "Inventory unverified" in raw
    assert "Saved Task README snapshot" in raw
    assert "Review saved artifacts" in raw
    assert "http://127.0.0.1" not in raw
    assert "Source artifacts were not found in this scan" in raw
    report.refresh(tmp_path, task_roots=["empty"], jobs_roots=["empty"])
    raw = (tmp_path / ".eval-author/README.md").read_text()
    assert "No tasks match." in raw
    assert "No recorded evaluation jobs." in raw


def test_markdown_runs_include_error_invalid_pending_and_dates(tmp_path):
    run_path = job(tmp_path, total=4)
    trial(tmp_path, run_path + "/error", error={"exception_type": "Timeout"})
    trial(tmp_path, run_path + "/invalid", trial_id="invalid")
    trial(tmp_path, run_path + "/pending", finished=False)
    metadata = rule(run_path)
    metadata["runs"][run_path]["invalid_attempts"] = {"invalid": "Invalid evidence"}
    report.refresh(tmp_path, patch=metadata)
    raw = (tmp_path / ".eval-author/README.md").read_text()
    for label in ("1 execution errors", "1 invalid", "2 pending or unscored", "incomplete", "Score unavailable"):
        assert label in raw
    assert "0/4 passed" not in raw


def test_markdown_render_failure_preserves_both_outputs(tmp_path, monkeypatch):
    path = task(tmp_path)
    report.refresh(tmp_path)
    paths = [tmp_path / ".eval-author" / name for name in ("report.html", "README.md")]
    before = [p.read_bytes() for p in paths]
    write(tmp_path, path + "/README.md", "## Behavior under test\nNew content")

    def fail(_):
        raise report.ReportError("Synthetic Markdown rendering failure")

    monkeypatch.setattr(report, "render_markdown", fail)
    with pytest.raises(report.ReportError, match="Synthetic"):
        report.refresh(tmp_path)
    assert [p.read_bytes() for p in paths] == before
    assert not (tmp_path / ".eval-author/.report.lock").exists()


def test_readme_output_symlink_is_not_followed(tmp_path):
    report.refresh(tmp_path)
    output = tmp_path / ".eval-author/README.md"
    original = (tmp_path / ".eval-author/report.html").read_bytes()
    outside = write(tmp_path, "untouched.md", "Do not overwrite")
    output.unlink()
    try:
        output.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    with pytest.raises(report.ReportError, match="Symlink"):
        report.refresh(tmp_path)
    assert outside.read_text() == "Do not overwrite"
    assert (tmp_path / ".eval-author/report.html").read_bytes() == original


def test_interrupted_publication_is_repaired_on_next_refresh(tmp_path, monkeypatch):
    path = task(tmp_path, readme="## Behavior under test\nBefore")
    report.refresh(tmp_path)
    html_path = tmp_path / ".eval-author/report.html"
    md_path = tmp_path / ".eval-author/README.md"
    html_before = html_path.read_bytes()
    write(tmp_path, path + "/README.md", "## Behavior under test\nAfter")
    replace = report.os.replace

    def fail_html(source, destination):
        if destination == html_path:
            raise OSError("Synthetic interrupted HTML publication")
        replace(source, destination)

    with monkeypatch.context() as m:
        m.setattr(report.os, "replace", fail_html)
        with pytest.raises(OSError, match="Synthetic"):
            report.refresh(tmp_path)
    assert html_path.read_bytes() == html_before
    assert "After" in md_path.read_text()
    assert not list(html_path.parent.glob(".report-*"))
    report.refresh(tmp_path)
    data, _ = report.previous_report(html_path)
    assert data["tasks"][0]["behavior"] == "After"
    assert md_path.read_text() == report.render_markdown(data)


def test_concurrent_markdown_edit_is_not_overwritten(tmp_path, monkeypatch):
    task(tmp_path)
    report.refresh(tmp_path)
    html = tmp_path / ".eval-author/report.html"
    markdown = tmp_path / ".eval-author/README.md"
    before = html.read_bytes()
    render = report.render_markdown

    def concurrent_edit(data):
        markdown.write_text("Concurrent edit")
        return render(data)

    monkeypatch.setattr(report, "render_markdown", concurrent_edit)
    with pytest.raises(report.ReportError, match="changed during refresh"):
        report.refresh(tmp_path)
    assert markdown.read_text() == "Concurrent edit"
    assert html.read_bytes() == before
