# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run-level outcomes and control provenance, using synthetic offline artifacts."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess

import pytest
from test_html_report import SCRIPTS, job, report, rule, trial, write


def control_round(
    root, revision, *, supersedes=None, rewards=None, unsupported=False, case_names=None, agent_case=False
):
    group = ".eval-author/jobs/control-checks"
    folder = ".eval-author/task-measurements/example"
    expected = {"reference": 1, "empty": 0, "incorrect": 0, "side_effect": 0}
    checksum = "synthetic-task-" + revision
    cases = {
        (case_names or {}).get(name, name): {
            "kind": name,
            "checks": [{"pointer": "/verifier_result/rewards/reward", "min": reward, "max": reward}],
            "health": [{"pointer": "/exception_info", "equals": None}],
        }
        for name, reward in expected.items()
    }
    if unsupported:
        for case in cases.values():
            case["health"] = [{"pointer": "/other_health", "equals": True}]
    if agent_case:
        cases["evaluation"] = {**cases["reference"], "kind": "agent"}
    manifest = write(
        root,
        f"{folder}/revision-{revision}.json",
        {
            "schema": "nemo.eval_author.task_evidence.v1",
            "harbor_task_checksum": checksum,
            "contract": {"provider": "harbor", "cases": cases},
            "repairs": [{"sha256": supersedes}] if supersedes else [],
        },
    )
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    for name, expected_reward in expected.items():
        path = f"{group}/{name}-{revision}"
        data = trial(root, path, reward=(rewards or {}).get(name, expected_reward))
        data["task_checksum"] = checksum
        result = write(root, path + "/result.json", data)
        write(
            root,
            f"{folder}/{name}-{revision}.json",
            {
                "schema": "nemo.eval_author.task_evidence.v1",
                "case": (case_names or {}).get(name, name),
                "native_run_id": data["id"],
                "manifest": "/previous/checkout/" + manifest.name,
                "manifest_sha256": digest,
                "result": "/previous/checkout/" + path + "/result.json",
                "result_sha256": hashlib.sha256(result.read_bytes()).hexdigest(),
            },
        )
    return group, digest


def test_outcomes_partition_planned_trials_without_changing_partial_credit(tmp_path):
    path = job(tmp_path, total=9)
    trial(tmp_path, path + "/pass")
    trial(tmp_path, path + "/partial", reward=0.5)
    trial(tmp_path, path + "/error", error={"exception_type": "Timeout"}, reward=1)
    trial(tmp_path, path + "/invalid", trial_id="invalid")
    trial(tmp_path, path + "/running", finished=False)
    no_score = trial(tmp_path, path + "/unscored")
    no_score["verifier_result"] = None
    write(tmp_path, path + "/unscored/result.json", no_score)
    native = json.loads((tmp_path / path / "result.json").read_text())
    native["stats"] = {"n_pending_trials": 1, "n_cancelled_trials": 1, "n_retries": 2}
    write(tmp_path, path + "/result.json", native)
    patch = rule(path)
    patch["runs"][path]["invalid_attempts"] = {"invalid": "Known invalid evidence"}
    run = report.refresh(tmp_path, patch=patch)["runs"][0]
    assert run["reward_sum"] == 1.5
    assert run["score_max"] == 2
    assert run["passed"] == 1 and run["failed"] == 1
    assert sum(run["outcomes"].values()) == 9
    for key in ["passed", "failed", "errors", "invalid", "running", "pending", "cancelled", "unscored", "unknown"]:
        assert run["outcomes"][key] == 1
    assert run["retries"] == 2
    assert "2 retries" in " ".join(run["detail_lines"])
    assert run["duration_seconds"] == 60
    assert "completed without a score" in run["outcome_summary"]


def test_job_reported_errors_remain_visible_without_trial_results(tmp_path):
    path = job(tmp_path, total=3)
    native = json.loads((tmp_path / path / "result.json").read_text())
    native["stats"] = {"n_errored_trials": 2, "n_cancelled_trials": 1}
    write(tmp_path, path + "/result.json", native)
    run = report.refresh(tmp_path, patch=rule(path))["runs"][0]
    assert run["errors"] == 2
    assert run["outcomes"]["cancelled"] == 1
    assert run["passed"] is None
    assert run["scored"] == 0
    assert "without retained trial results" in " ".join(run["issues"])


def test_step_errors_are_not_scored_failures(tmp_path):
    path = job(tmp_path, total=1)
    data = trial(tmp_path, path + "/attempt", reward=0)
    data["step_results"] = [{"exception_info": {"exception_type": "VerifierError"}}]
    write(tmp_path, path + "/attempt/result.json", data)
    run = report.refresh(tmp_path, patch=rule(path))["runs"][0]
    assert run["errors"] == 1 and run["failed"] == 0 and run["scored"] == 0


def test_summaries_persist_and_expected_failures_require_explicit_sources(tmp_path):
    path = job(tmp_path, total=1)
    trial(tmp_path, path + "/nop", agent="nop", reward=0)
    fields = {**rule(path)["runs"][path], "role": "control", "summary": "The no-op is rejected.", "expected_reward": 0}
    with pytest.raises(report.ReportError, match="source_refs"):
        report.refresh(tmp_path, patch={"runs": {path: fields}})
    write(tmp_path, "control-contract.md", "No action must receive reward zero.")
    fields["source_refs"] = ["control-contract.md"]
    first = report.refresh(tmp_path, patch={"runs": {path: fields}})
    assert report.refresh(tmp_path) == first
    run = first["runs"][0]
    assert run["failed"] == 1  # Raw score classification remains intact.
    assert run["outcomes"]["failed"] == 0
    assert run["outcomes"]["expected_failures"] == 1
    assert run["expectation"] is True
    (tmp_path / "control-contract.md").unlink()
    refreshed = report.refresh(tmp_path)["runs"][0]
    assert refreshed["expectation"] is None
    assert refreshed["outcomes"]["expected_failures"] == 0
    assert refreshed["summary"] != fields["summary"]
    assert "source is unavailable" in " ".join(refreshed["issues"])


@pytest.mark.parametrize("value", [None, 0.5])
def test_scores_without_pass_rules_are_never_guessed(tmp_path, value):
    path = job(tmp_path, total=1)
    data = trial(tmp_path, path + "/attempt", reward=0.5)
    if value is None:
        data["verifier_result"] = None
        write(tmp_path, path + "/attempt/result.json", data)
    run = report.refresh(tmp_path)["runs"][0]
    assert run["passed"] is None
    assert run["failed"] == 0
    assert run["outcomes"]["scored"] == (value is not None)
    assert "pass/fail unavailable" in run["outcome_summary"]


def test_legacy_state_without_summary_fields_refreshes_from_native_evidence(tmp_path):
    path = job(tmp_path, total=1)
    trial(tmp_path, path + "/one")
    data = report.refresh(tmp_path, patch=rule(path))
    for key in [
        "outcomes",
        "outcome_summary",
        "summary",
        "duration_seconds",
        "retries",
        "expectation",
        "expected_reward",
        "detail_lines",
    ]:
        del data["runs"][0][key]
    del data["control_groups"]
    write(tmp_path, ".eval-author/.working/report/state.json", data)
    assert report.refresh(tmp_path)["runs"][0]["outcomes"]["passed"] == 1


def test_controls_use_bound_latest_revision_and_preserve_raw_trials(tmp_path):
    group, previous = control_round(tmp_path, "r1")
    control_round(tmp_path, "r2", supersedes=previous)
    data = report.refresh(tmp_path)
    assert data["runs"] == []
    assert len(data["standalone_trials"]) == 8
    assert len(data["control_groups"]) == 1
    row = data["control_groups"][0]
    assert row["path"] == group
    assert row["total"] == 4 and row["history_count"] == 4
    assert row["expectations_met"] == 4 and row["expectations_unmet"] == 0
    assert row["passed"] == 1 and row["failed"] == 3
    assert row["outcomes"]["expected_failures"] == 3
    assert row["outcomes"]["failed"] == 0
    assert len(row["member_paths"]) == 8
    markdown = (tmp_path / ".eval-author/README.md").read_text()
    assert "### Task controls" in markdown and "### Legacy trials" not in markdown
    assert "4/4 control expectations met" in markdown
    assert report.refresh(tmp_path) == data


def test_control_regression_is_not_hidden_by_an_expected_failure(tmp_path):
    control_round(tmp_path, "r2", rewards={"incorrect": 1})
    row = report.refresh(tmp_path)["control_groups"][0]
    assert row["expectations_met"] == 3
    assert row["expectations_unmet"] == 1
    assert row["expectation"] is False
    assert "not met" in row["summary"]


def test_control_case_names_do_not_define_their_kind(tmp_path):
    control_round(tmp_path, "r2", case_names={"reference": "gold", "empty": "noop"})
    row = report.refresh(tmp_path)["control_groups"][0]
    assert row["passed"] == 1
    assert row["outcomes"]["expected_failures"] == 3
    assert sum(row["outcomes"].values()) == 4


def test_native_agent_receipts_do_not_hide_task_controls(tmp_path):
    _, digest = control_round(tmp_path, "r2", agent_case=True)
    path = job(tmp_path, total=1)
    trial(tmp_path, path + "/attempt")
    native = json.loads((tmp_path / path / "result.json").read_text())
    write(
        tmp_path,
        ".eval-author/task-measurements/example/agent-r2.json",
        {
            "schema": "nemo.eval_author.task_evidence.v1",
            "case": "evaluation",
            "native_run_id": native["id"],
            "manifest": "/previous/checkout/revision-r2.json",
            "manifest_sha256": digest,
            "result": "/previous/checkout/" + path + "/result.json",
        },
    )
    data = report.refresh(tmp_path)
    assert len(data["control_groups"]) == 1
    assert len(data["runs"]) == 1
    assert data["warnings"] == []


def test_missing_control_results_remain_visible_when_scanning_the_whole_repository(tmp_path):
    _, previous = control_round(tmp_path, "r1")
    control_round(tmp_path, "r2", supersedes=previous)
    shutil.rmtree(tmp_path / ".eval-author/jobs/control-checks/reference-r2")
    data = report.refresh(tmp_path, jobs_roots=["."])
    assert data["control_groups"] == []
    assert data["warnings"]


@pytest.mark.parametrize(
    "damage",
    [
        "result",
        "manifest",
        "missing_case",
        "missing_result",
        "unsupported_contract",
        "unsupported_kind",
        "ambiguous_revision",
    ],
)
def test_unverifiable_latest_controls_do_not_fall_back_to_old_success(tmp_path, damage):
    _, previous = control_round(tmp_path, "r1")
    control_round(
        tmp_path,
        "r2",
        supersedes=None if damage == "ambiguous_revision" else previous,
        unsupported=damage == "unsupported_contract",
    )
    folder = tmp_path / ".eval-author/task-measurements/example"
    if damage == "result":
        path = ".eval-author/jobs/control-checks/reference-r2/result.json"
        result = json.loads((tmp_path / path).read_text())
        result["verifier_result"]["rewards"]["reward"] = 0
        write(tmp_path, path, result)
    elif damage == "manifest":
        (folder / "revision-r2.json").write_text("{}")
    elif damage == "missing_case":
        (folder / "reference-r2.json").unlink()
    elif damage == "missing_result":
        shutil.rmtree(tmp_path / ".eval-author/jobs/control-checks/reference-r2")
    elif damage == "unsupported_kind":
        manifest_path = folder / "revision-r2.json"
        manifest = json.loads(manifest_path.read_text())
        for case in manifest["contract"]["cases"].values():
            case["kind"] = "unsupported"
        manifest_path.write_text(json.dumps(manifest))
        digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        for name in manifest["contract"]["cases"]:
            receipt_path = folder / f"{name}-r2.json"
            receipt = json.loads(receipt_path.read_text())
            receipt["manifest_sha256"] = digest
            receipt_path.write_text(json.dumps(receipt))
    data = report.refresh(tmp_path)
    assert data["control_groups"] == []
    assert len(data["standalone_trials"]) == (7 if damage == "missing_result" else 8)
    assert data["warnings"]


def test_named_control_folders_alone_do_not_establish_grouping(tmp_path):
    for name, reward in [("reference", 1), ("empty", 0)]:
        trial(tmp_path, ".eval-author/jobs/example-controls/" + name, reward=reward)
    data = report.refresh(tmp_path)
    assert data["control_groups"] == []
    assert len(data["standalone_trials"]) == 2


def test_browser_shows_categorical_outcomes_without_reward_column(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise browser rendering")
    path = job(tmp_path, total=5)
    trial(tmp_path, path + "/pass")
    trial(tmp_path, path + "/fail", reward=0.5)
    trial(tmp_path, path + "/error", error={"exception_type": "Timeout"})
    trial(tmp_path, path + "/running", finished=False)
    control_round(tmp_path, "r2")
    data = report.refresh(tmp_path, patch=rule(path))
    source = (SCRIPTS / "report-template.html").read_text()
    helper = source[source.index("function runRow(") : source.index("byId('search').addEventListener")]
    program = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
class Element{
  constructor(tag,text,className){this.tag=tag;this.textContent=text||'';this.className=className;this.children=[];this.style={};this.dataset={};this.attrs={};}
  append(...items){this.children.push(...items);}
  replaceChildren(){this.children=[];}
  setAttribute(key,value){this.attrs[key]=value;}
}
const ids={};const byId=id=>ids[id]||(ids[id]=new Element('div'));
const context={data:input.data,byId,element:(...args)=>new Element(...args),viewerAction:label=>new Element('button',label)};
vm.createContext(context);vm.runInContext(input.helper+'drawRuns();',context);
const descendants=node=>[node,...node.children.flatMap(descendants)];
const nodes=descendants(ids['run-rows']);
assert.equal(ids['run-rows'].children[0].children.length,4);
assert(!nodes.some(n=>n.attrs.role==='meter'||n.className==='score-fill'));
const bar=nodes.find(n=>n.attrs.role==='img');assert(bar.attrs['aria-label'].includes('1 execution error'));
assert.equal(bar.children.length,5);
assert(bar.children.some(n=>n.className.includes('outcome-errors')));
assert(bar.children.some(n=>n.className.includes('outcome-running')));
assert(bar.children.some(n=>n.className.includes('outcome-unknown')));
assert.equal(ids['control-rows'].children.length,1);
assert.equal(ids['legacy-trials'].hidden,true);
assert(descendants(ids['control-rows']).some(n=>(n.className||'').includes('outcome-expected_failures')));
input.data.runs[0].status='unverified';vm.runInContext('drawRuns();',context);
assert(!descendants(ids['run-rows']).some(n=>n.attrs.role==='meter'||n.attrs.role==='img'));
"""
    result = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"helper": helper, "data": data}),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
