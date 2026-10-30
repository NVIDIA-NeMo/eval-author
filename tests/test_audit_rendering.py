# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_audit_resume_rendering import presentation
from test_audit_state import approved, measured, reviewed, selected

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures/audit-conversations"


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return tuple(importlib.import_module(name) for name in ("journey_state", "audit_state", "conversation_renderer"))


def state_at(modules, milestone):
    payload = presentation(modules)
    audit = modules[1]
    if milestone == "suites":
        audit.record(payload["state"])["suites"]["candidates"] = ["suites/refund"]
        audit.record(payload["state"])["pending"] = None
        return payload
    builder = {"selected": selected, "approved": approved, "measured": measured, "reviewed": reviewed}[milestone]
    domain, _, observed = builder(audit)
    payload["state"]["scopes"] = domain["scopes"]
    payload["facts"]["audit_observations"] = observed
    payload["state"]["journey_status"]["audit"] = "complete" if milestone == "reviewed" else "in_progress"
    return payload


def domain(payload, modules):
    return modules[1].record(payload["state"])


def progress(payload, modules):
    return modules[1].derive(payload["state"], payload["facts"]["audit_observations"])


def remember_question(payload, modules, question=None):
    if question is None:
        flow = importlib.import_module("conversation_flows.audit")
        question = flow.decision_question(payload["stage"], domain(payload, modules), payload["facts"])
    current = progress(payload, modules)
    stage = "findings_review" if payload["stage"] == "audit_findings" else current["stage"]
    domain(payload, modules)["pending"] = {
        "stage": stage,
        "digest": current["checkpoint_digests"][stage],
        "question": question,
        "source": "Presented this checkpoint to the user",
    }


def report_links(payload, modules, *, intended=False, findings=False):
    lifecycle = domain(payload, modules)
    if not lifecycle["findings"]:
        report = {
            "path": ".eval-author/audit-coverage-report.md",
            "digest": modules[1].fingerprint("Synthetic preliminary coverage report"),
        }
        lifecycle["findings"] = {"report": report, "source": "Saved the preliminary coverage report"}
        payload["facts"]["audit_observations"]["files"][report["path"]] = report["digest"]
    target = str(Path(payload["state"]["repository"]) / lifecycle["findings"]["report"]["path"])
    links = {"report": {"target": target}}
    if intended:
        links["intended_coverage"] = {"target": target, "fragment": "intended-coverage"}
    if findings:
        links["findings"] = {"target": target, "fragment": "findings-and-evidence"}
    payload["facts"]["links"] = links


def set_blocker(payload, question, step, reason, needed):
    payload["state"]["blockers"] = [{"id": "run-evidence", "phase": "audit", "message": reason}]
    payload["state"]["pending_question"] = question
    payload["facts"]["blockers"] = [{"id": "run-evidence", "step": step, "reason": reason, "needed": needed}]


def checkpoint(modules, stage):
    milestone = {
        "suites": "suites",
        "traces": "selected",
        "checks": "selected",
        "measurement": "approved",
        "findings": "measured",
        "next_action": "reviewed",
        "blockers": "measured",
    }[stage]
    payload = state_at(modules, milestone)
    payload["stage"] = "audit_" + stage
    facts = payload["facts"]
    if stage == "suites":
        facts["suites"] = [
            {
                "path": "suites/refund",
                "label": "Refund eligibility",
                "description": "Checks eligible refunds and declines requests outside the refund window.",
            }
        ]
    elif stage == "traces":
        domain(payload, modules)["traces"] = modules[1].new_record()["traces"]
        domain(payload, modules)["trace_files"] = []
    elif stage == "checks":
        report_links(payload, modules, intended=True)
        facts.update(
            checks={
                kind: [{"check": check, "evidence": evidence}]
                for kind, check, evidence in [
                    ("tools", "Look up refund eligibility.", "A recorded eligibility lookup."),
                    ("capabilities", "Complete an eligible refund.", "The correct refund transaction."),
                    ("failure_cases", "Decline unverified requests.", "No refund after identity verification fails."),
                    ("grading", "Reject misleading success messages.", "A grader checking transaction state."),
                ]
            },
            validation="passed",
            report_action="created",
            limits=["Coverage has not been measured yet."],
        )
    elif stage == "measurement":
        facts["progress"] = "Checking selected refund runs with the approved tool and behavior measurements."
    elif stage == "findings":
        report_links(payload, modules, findings=True)
        facts.update(
            conclusion=(
                "The selected traces exercise successful refunds. I recommend improving the grader’s transaction "
                "checks and adding a scenario for failed identity verification."
            ),
            findings=[
                {
                    "kind": "strength",
                    "label": "Eligible refunds are exercised",
                    "evidence": "Selected traces show the eligibility lookup and the expected refund transaction.",
                },
                {
                    "kind": "grading_weakness",
                    "label": "The grader checks a success keyword",
                    "evidence": "The inspected grader checks the final message without verifying transaction state.",
                },
                {
                    "kind": "missing_scenario",
                    "label": "Failed identity verification has no selected case",
                    "evidence": "The selected definitions contain no refund request after failed identity verification.",
                },
            ],
            limits=[
                "These findings apply only to the selected refund suite and traces.",
                "The grading weakness is based on inspecting grader logic; this audit has not tested whether "
                "an incorrect result would receive a passing grade.",
            ],
            recommendations=[
                {"kind": "improve", "label": label, "reason": reason}
                for label, reason in [
                    (
                        "grader improvement",
                        "Verify the refund transaction instead of accepting a keyword in the final response.",
                    ),
                    ("new scenario", "Exercise a refund request after identity verification fails."),
                ]
            ],
            report_action="updated",
        )
    elif stage == "next_action":
        report_links(payload, modules)
    elif stage == "blockers":
        lifecycle = domain(payload, modules)
        lifecycle["measurement"].update(status="partial", source="One selected run has no usable behavior measurement")
        set_blocker(
            payload,
            "Should I continue with limited findings, or wait for the missing run evidence?",
            "Measure coverage",
            "One selected run has incomplete measurements.",
            "Choose limited findings or supply the missing run evidence.",
        )
    if stage in {"suites", "traces", "checks", "findings"}:
        remember_question(payload, modules)
    return payload


def revise_report(payload, modules, content):
    report = domain(payload, modules)["findings"]["report"]
    report["digest"] = modules[1].fingerprint(content)
    payload["facts"]["audit_observations"]["files"][report["path"]] = report["digest"]


def continuation(modules, scenario):
    base = {
        "revised_checks": "checks",
        "corrected_findings": "findings",
        "no_change_findings": "findings",
        "single_recommendation": "findings",
        "missing_traces": "blockers",
        "returning_findings": "findings",
        "selected_improvement": "next_action",
        "limited_findings": "findings",
        "partial_findings": "findings",
    }[scenario]
    payload = checkpoint(modules, base)
    lifecycle = domain(payload, modules)
    facts = payload["facts"]
    if scenario == "revised_checks":
        facts.update(
            report_action="updated",
            changes=["Require transaction-state assertions in refund grading after the user's correction."],
        )
        facts["checks"]["grading"] = [
            {
                "check": "A success message alone must not earn a passing grade.",
                "evidence": "The actual refund transaction must match the expected account and amount.",
            }
        ]
        facts["audit_observations"]["files"][lifecycle["specification"]] = modules[1].fingerprint(
            "Revised refund checks"
        )
    elif scenario == "corrected_findings":
        facts["conclusion"] = (
            "I corrected the report: failed identity verification already has a selected case and trace. "
            "I recommend improving the grader’s transaction checks."
        )
        facts["findings"] = [
            {
                "kind": "strength",
                "label": "Failed identity verification is already exercised",
                "evidence": "Rechecking the selected definitions and traces confirmed the existing rejection scenario.",
            },
            facts["findings"][1],
        ]
        facts["recommendations"] = facts["recommendations"][:1]
    elif scenario == "no_change_findings":
        facts["conclusion"] = (
            "The selected traces support the reviewed refund checks. "
            "I found no evaluation changes to recommend for this scope."
        )
        facts["findings"] = facts["findings"][:1]
        facts["limits"] = ["These findings apply only to the selected refund suite and traces."]
        facts["recommendations"] = []
    elif scenario == "single_recommendation":
        facts["conclusion"] = (
            "The selected traces exercise successful refunds. I recommend strengthening the grader’s transaction checks."
        )
        facts["findings"] = facts["findings"][:2]
        facts["recommendations"] = facts["recommendations"][:1]
    elif scenario == "missing_traces":
        missing = lifecycle["trace_files"][0]
        facts["audit_observations"]["files"][missing] = None
        set_blocker(
            payload,
            "Should I use another trace source, or revise this audit’s scope to continue without these traces?",
            "Restore selected traces",
            f"Selected trace {missing} is missing. Prior measurements no longer establish current coverage.",
            "Restore the selected trace, choose another source, or confirm a revised scope before reviewing checks again.",
        )
    elif scenario == "returning_findings":
        payload["stage"] = "audit_resume"
        payload["facts"]["report_action"] = "unchanged"
        return payload
    elif scenario == "selected_improvement":
        lifecycle["next_action"] = {
            "kind": "improve",
            "findings_digest": progress(payload, modules)["findings_digest"],
            "source": "User accepted the findings and chose to plan both improvements",
        }
    elif scenario in {"limited_findings", "partial_findings"}:
        lifecycle["measurement"].update(
            status="partial" if scenario == "partial_findings" else "deferred",
            source=(
                "One selected trace failed capability measurement"
                if scenario == "partial_findings"
                else "The user explicitly deferred missing capability measurements and requested limited findings."
            ),
        )
        facts.update(
            conclusion=(
                "The selected traces have tool-call measurements, but capability coverage is still unknown. "
                "I recommend collecting the missing evidence before deciding on evaluation changes."
            ),
            findings=[
                {
                    "kind": "strength",
                    "label": "Refund tools were exercised in the measured subset",
                    "evidence": "Successful tool-call measurements show the selected refund lookup and transaction.",
                },
                {
                    "kind": "insufficient_evidence",
                    "label": "Capability coverage remains unknown",
                    "evidence": "One selected trace lacks usable capability measurements.",
                },
            ],
            limits=[
                (
                    "One selected trace still lacks usable capability measurements."
                    if scenario == "partial_findings"
                    else "Measurement was explicitly deferred for the failed inputs."
                ),
                "Unmeasured behavior is unknown, not evidence that a test is missing.",
            ],
            recommendations=[
                {
                    "kind": "collect_evidence",
                    "label": "remaining trace evidence",
                    "reason": "Complete capability measurement before treating unknown behavior as a coverage gap.",
                }
            ],
        )
    if base in {"checks", "findings"}:
        revise_report(payload, modules, scenario)
        remember_question(payload, modules)
    return payload


def forbid_io(*_args, **_kwargs):
    raise AssertionError("Audit rendering must use only the supplied state and observed facts")


@pytest.mark.parametrize("group", ["checkpoints", "continuations"])
def test_reviewed_transcripts_are_exact_and_render_without_side_effects(modules, monkeypatch, group):
    expected = json.loads((FIXTURES / (group + ".json")).read_text())
    build = checkpoint if group == "checkpoints" else continuation
    requests = [(scenario, build(modules, scenario)) for scenario in expected]
    state_helper, audit, renderer = modules
    for name in ("open", "read_text", "read_bytes", "write_text", "write_bytes"):
        monkeypatch.setattr(Path, name, forbid_io)
    for name in ("now", "load_state", "save", "update", "initialize"):
        monkeypatch.setattr(state_helper, name, forbid_io)
    monkeypatch.setattr(audit, "observe", forbid_io)
    monkeypatch.setattr(subprocess, "run", forbid_io)
    for scenario, payload in requests:
        original = copy.deepcopy(payload)
        assert renderer.render(payload) == expected[scenario], scenario
        assert renderer.render(copy.deepcopy(payload)) == expected[scenario], scenario
        assert payload == original


@pytest.mark.parametrize("stage", ["suites", "traces", "checks", "findings"])
def test_resume_replays_the_current_checkpoint(modules, stage):
    payload = checkpoint(modules, stage)
    expected = modules[2].render(payload)
    payload["stage"] = "audit_resume"
    assert modules[2].render(payload) == expected


@pytest.mark.parametrize("stage", ["suites", "traces", "checks", "measurement", "findings", "next_action"])
@pytest.mark.parametrize("observations", [False, True])
def test_onboarding_resume_rejects_downstream_audit_checkpoints(modules, stage, observations):
    payload = checkpoint(modules, stage)
    facts = {"audit_observations": payload["facts"]["audit_observations"]} if observations else {}
    with pytest.raises(ValueError, match="Use audit_resume"):
        modules[2].render({**payload, "stage": "resume", "facts": facts})


def test_partial_findings_can_be_reviewed_before_acceptance(modules):
    payload = continuation(modules, "partial_findings")
    original = copy.deepcopy(payload)
    current = progress(payload, modules)
    assert current["stage"] == "findings_review" and not current["measurement_complete"]
    assert "measurement" not in current["checkpoint_digests"]
    assert not current["findings_reviewed"] and not current["complete"]
    output = modules[2].render(payload)
    assert "Measurement is partial" in output
    assert "**Current audit step:** Review findings" in output
    assert "You’ve accepted the audit findings." not in output
    assert output.rstrip().endswith(domain(payload, modules)["pending"]["question"])
    assert payload == original
    payload["stage"] = "audit_resume"
    assert modules[2].render(payload) == output


@pytest.mark.parametrize("stage", ["audit_measurement", "audit_resume"])
@pytest.mark.parametrize("unavailable", [False, True])
def test_measurement_copy_does_not_claim_work_is_running(modules, stage, unavailable):
    payload = checkpoint(modules, "measurement")
    payload["stage"] = stage
    payload["facts"].pop("progress")
    lifecycle = domain(payload, modules)
    if unavailable:
        lifecycle["traces"].update(status="unavailable", paths=[], source="User confirmed traces are unavailable")
        lifecycle["trace_files"] = []
        lifecycle["checks_approval"]["digest"] = progress(payload, modules)["checks_digest"]
    output = modules[2].render(payload)
    assert "I’m inspecting" not in output and "I’m measuring" not in output
    if unavailable:
        assert "Coverage cannot be measured until usable traces are selected." in output
        assert "selected traces still need measurement" not in output
    else:
        assert "The selected traces still need measurement against the approved checks." in output


def test_trace_request_does_not_assume_suites_exist(modules):
    payload = checkpoint(modules, "traces")
    lifecycle = domain(payload, modules)
    lifecycle["suites"].update(status="none", paths=[], source="User confirmed no suites exist")
    lifecycle["suite_files"] = []
    output = modules[2].render(payload)
    assert "You confirmed that no evaluation suites exist." in output
    assert "from those suites" not in output
    assert "which tools and behaviors were actually exercised" in output


def test_deferral_provenance_is_not_rendered_as_a_reason(modules):
    payload = continuation(modules, "limited_findings")
    domain(payload, modules)["measurement"]["source"] = "INTERNAL_PROVENANCE: verbatim saved answer"
    remember_question(payload, modules)
    output = modules[2].render(payload)
    assert "INTERNAL_PROVENANCE" not in output and "verbatim saved answer" not in output
    assert "Measurement was explicitly deferred for the failed inputs." in output


@pytest.mark.parametrize("label,heading", [("grader improvement", "Grader improvement"), ("API grader", "API grader")])
def test_recommendation_headings_capitalize_without_changing_the_question(modules, label, heading):
    payload = continuation(modules, "single_recommendation")
    payload["facts"]["recommendations"][0]["label"] = label
    remember_question(payload, modules)
    output = modules[2].render(payload)
    assert f"1. **{heading}:**" in output
    assert f"plan the {label}?" in output


def test_empty_findings_are_rejected(modules):
    payload = checkpoint(modules, "findings")
    payload["facts"]["findings"] = []
    with pytest.raises(ValueError, match="at least one finding"):
        modules[2].render(payload)


@pytest.mark.parametrize("selection", ["suites", "traces"])
def test_report_links_reject_excluded_observations(modules, selection):
    payload = checkpoint(modules, "checks")
    lifecycle = domain(payload, modules)
    report = lifecycle["findings"]["report"]
    lifecycle[selection]["exclusions"] = [
        {"path": report["path"], "reason": "Excluded evidence", "source": "User excluded this path"}
    ]
    assert payload["facts"]["audit_observations"]["files"][report["path"]] == report["digest"]
    with pytest.raises(ValueError, match="observed digest before linking"):
        modules[2].render(payload)


@pytest.mark.parametrize(
    "stage,key",
    [
        ("suites", "findings"),
        ("traces", "intended_coverage"),
        ("checks", "findings"),
        ("measurement", "report"),
        ("findings", "intended_coverage"),
        ("next_action", "findings"),
        ("blockers", "intended_coverage"),
    ],
)
def test_stage_inapplicable_links_are_rejected(modules, stage, key):
    payload = checkpoint(modules, stage)
    report_links(payload, modules)
    report = payload["facts"]["links"]["report"]
    payload["facts"]["links"][key] = {**report, "fragment": "intended-coverage"}
    with pytest.raises(ValueError, match="audit links"):
        modules[2].render(payload)


@pytest.mark.parametrize("stage", ["measurement", "blockers"])
def test_report_announcements_allow_report_links_at_measurement_and_blockers(modules, stage):
    payload = checkpoint(modules, stage)
    report_links(payload, modules)
    payload["facts"]["report_action"] = "unchanged"
    target = payload["facts"]["links"]["report"]["target"]
    assert f"Review [Audit coverage report](<{target}>)." in modules[2].render(payload)


@pytest.mark.parametrize("stage", ["suites", "traces"])
@pytest.mark.parametrize("action,notice", [("created", "I created"), ("updated", "I updated"), ("unchanged", "Review")])
def test_selection_checkpoint_can_announce_a_report_without_advancing(modules, stage, action, notice):
    payload = checkpoint(modules, stage)
    report_links(payload, modules)
    payload["facts"]["report_action"] = action
    original = copy.deepcopy(payload)
    target = payload["facts"]["links"]["report"]["target"]
    output = modules[2].render(payload)
    assert f"{notice} [Audit coverage report](<{target}>)." in output
    assert output.rstrip().endswith(domain(payload, modules)["pending"]["question"])
    assert payload == original
    payload["stage"] = "audit_resume"
    assert modules[2].render(payload) == output


def test_selection_report_notice_requires_its_bound_report_link(modules):
    payload = checkpoint(modules, "suites")
    report_links(payload, modules)
    payload["facts"]["report_action"] = "created"
    payload["facts"].pop("links")
    with pytest.raises(ValueError, match="links.report"):
        modules[2].render(payload)


@pytest.mark.parametrize("stage", ["suites", "traces", "checks", "findings"])
@pytest.mark.parametrize("change", ["missing", "stale", "contradictory"])
def test_decision_requires_the_current_saved_question(modules, stage, change):
    payload = checkpoint(modules, stage)
    lifecycle = domain(payload, modules)
    if change == "missing":
        lifecycle["pending"] = None
    elif change == "stale":
        lifecycle["pending"]["digest"] = "0" * 64
    else:
        lifecycle["pending"]["question"] = "May I skip the unresolved decision?"
    with pytest.raises(ValueError):
        modules[2].render(payload)


@pytest.mark.parametrize(
    "stage,path,value",
    [
        ("findings", ("revision",), 0),
        ("findings", ("state", "journey"), "create"),
        ("findings", ("state", "ethos", "stage"), "review"),
        ("findings", ("state", "prerequisites"), {}),
        ("findings", ("facts", "audit_observations"), None),
        ("findings", ("ready",), True),
        ("findings", ("facts", "complete"), True),
        ("findings", ("facts", "links", "report", "target"), "/synthetic/another-report.md"),
        ("checks", ("facts", "validation"), "failed"),
        ("checks", ("facts", "links", "intended_coverage", "fragment"), ""),
        ("checks", ("facts", "report_action"), "published"),
        ("suites", ("facts", "suites"), []),
        ("blockers", ("facts", "blockers"), []),
        ("blockers", ("facts", "blockers", 0, "id"), "not-saved"),
        ("blockers", ("state", "blockers", 0, "phase"), "create"),
    ],
)
def test_renderer_rejects_conflicting_authority_and_unbound_facts(modules, stage, path, value):
    payload = checkpoint(modules, stage)
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        modules[2].render(payload)


@pytest.mark.parametrize("artifact", ["report", "measurement"])
def test_old_findings_cannot_render_after_evidence_disappears(modules, artifact):
    payload = checkpoint(modules, "findings")
    lifecycle = domain(payload, modules)
    ref = lifecycle["findings"]["report"] if artifact == "report" else lifecycle["measurement"]["evidence"][0]
    payload["facts"]["audit_observations"]["files"][ref["path"]] = None
    with pytest.raises(ValueError):
        modules[2].render(payload)


@pytest.mark.parametrize("question_source", ["blocker", "old_checkpoint"])
def test_blocker_preempts_the_current_approval_question_in_view_and_render(modules, monkeypatch, question_source):
    payload = checkpoint(modules, "checks")
    state_helper, audit, renderer = modules
    old_question = domain(payload, modules)["pending"]["question"]
    question = "Should I use another run folder or continue with limited findings?"
    observed = payload["facts"]["audit_observations"]
    payload["facts"] = {"audit_observations": observed}
    payload["stage"] = "audit_blockers"
    set_blocker(payload, question, "Select usable run evidence", "Source needs clarification.", "Choose a replacement.")
    if question_source == "old_checkpoint":
        payload["state"]["pending_question"] = old_question
    original = copy.deepcopy(payload)
    monkeypatch.setattr(audit, "observe", lambda *_: observed)
    projected = state_helper.view(payload["state"])
    assert projected["state"]["pending_question"] == (question if question_source == "blocker" else None)
    for state in (payload["state"], projected["state"]):
        output = renderer.render({**payload, "state": state})
        assert "Are these the right checks and evidence requirements" not in output
        assert (question in output) == (question_source == "blocker")
    assert payload == original


@pytest.mark.parametrize("status", ["pending", "in_progress", "complete"])
def test_deferred_blocker_only_preempts_a_started_audit(modules, status):
    payload = checkpoint(modules, "suites")
    payload["state"]["journey_status"]["audit"] = status
    payload["state"]["blockers"] = [{"id": "data", "phase": "audit", "message": "Needed for later audit work."}]
    question = domain(payload, modules)["pending"]["question"]
    if status != "pending":
        payload["stage"] = "audit_blockers"
        payload["facts"] = {"audit_observations": payload["facts"]["audit_observations"]}
        question = "Which source resolves this audit dependency?"
        set_blocker(
            payload, question, "Resolve audit dependency", "This dependency is now required.", "Supply the source."
        )
        payload["state"]["blockers"][0]["id"] = "data"
        payload["facts"]["blockers"][0]["id"] = "data"
    output = modules[2].render(payload)
    assert output.rstrip().endswith(question)
    if status == "pending":
        assert (
            "- [ ] **Review evaluation coverage, identify gaps, and recommend improvements** — **We're here**" in output
        )


def test_explicit_pending_next_action_still_resumes(modules):
    payload = checkpoint(modules, "next_action")
    question = "Would you like to finish here or plan the proposed grader improvement?"
    remember_question(payload, modules, question)
    payload["stage"] = "audit_resume"
    assert modules[2].render(payload).rstrip().endswith(question)


@pytest.mark.parametrize("change", ["missing", "changed"])
def test_accepted_partial_findings_resume_without_a_new_question(modules, change):
    payload = checkpoint(modules, "next_action")
    lifecycle = domain(payload, modules)
    lifecycle["measurement"].update(status="partial", source="User accepted limited findings and deferred the rest.")
    lifecycle["findings_review"]["digest"] = progress(payload, modules)["findings_digest"]
    output = modules[2].render(payload)
    payload["stage"] = "audit_resume"
    assert modules[2].render(payload) == output
    assert "You’ve accepted the audit findings." in output and "?" not in output
    assert "Measurement is partial; the successful subset does not complete the agreed scope." in output
    assert "- [ ] **Review evaluation coverage, identify gaps, and recommend improvements**" in output
    evidence = lifecycle["measurement"]["evidence"][0]
    payload["facts"]["audit_observations"]["files"][evidence["path"]] = (
        None if change == "missing" else modules[1].fingerprint("Changed evidence")
    )
    payload["facts"].pop("links")
    resumed = modules[2].render(payload)
    payload["stage"] = "audit_measurement"
    assert resumed == modules[2].render(payload)
    assert "You’ve accepted the audit findings." not in resumed
    assert "Saved measurements are missing, changed, or no longer applicable" in resumed
    assert "Measurement is partial" not in resumed


def test_plain_text_findings_cannot_inject_html_or_links(modules):
    payload = checkpoint(modules, "findings")
    payload["facts"]["findings"][0]["label"] = '<script>alert("audit")</script> [click](https://example.invalid)'
    output = modules[2].render(payload)
    assert "<script>" not in output and "[click](https://example.invalid)" not in output
    assert "&lt;script&gt;" in output


def test_cli_matches_the_pure_renderer(modules):
    payload = checkpoint(modules, "findings")
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "conversation_renderer.py"), "--input", "-"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0 and result.stderr == ""
    assert result.stdout == modules[2].render(payload)


def test_audit_checkpoints_have_no_stateless_preview(modules):
    with pytest.raises(ValueError):
        modules[2].render({"stage": "audit_findings", "journey": "audit", "ready": True})
