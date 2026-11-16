# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import hashlib
import importlib
import json


def setup_ready(state, repo, *, journey="create", setup="gym"):
    state.initialize(repo, agent="assistant", journey=journey, setup=setup)
    return state.update(
        repo, {"prerequisites": {key: {"status": "confirmed", "source": "user reply 2"} for key in state.PREREQUISITES}}
    )


def ethos_ready(state, repo):
    (repo / "ETHOS.md").write_text("# Confirmed agent intent\n")
    return state.confirm_ethos(
        repo,
        reviewed_digest=state.ethos_digest(repo),
        source="user reviewed saved document in reply 4",
        validation="ethos-write parser passed",
    )


def expansion_start(modules, repo, *, handoff=False):
    state, expansion, _ = modules
    setup_ready(state, repo, journey="expand")
    saved = ethos_ready(state, repo)
    legacy = "Earlier requested additions"
    scope = {"legacy_context": legacy, "priorities": ["refunds"]}
    if handoff:
        audit = importlib.import_module("audit_state")
        lifecycle = audit.new_record()
        lifecycle["findings"] = {
            "report": {"path": ".eval-author/audit-coverage-report.md", "digest": "b" * 64},
            "content": {"path": ".eval-author/.working/audit/report-content.json", "digest": "c" * 64},
            "inputs": [],
            "source": "Reviewed audit findings",
        }
        lifecycle["next_action"] = {
            "kind": "improve",
            "recommendation_ids": ["R1", "R3"],
            "findings_digest": "a" * 64,
            "source": "User selected these audit recommendations",
        }
        audit.retain_handoff({"scopes": {}}, {"scopes": {"audit": {"lifecycle": lifecycle}, "expand": scope}})
    saved["scopes"]["expand"] = scope
    state.save(repo, saved)
    value = expansion.new_record(
        saved, question="Should the refund task cover both full and partial refunds?", source="User request"
    )
    return state.update(
        repo,
        {"scopes": {"expand": {"lifecycle": value}}, "pending_question": value["pending"]["question"]},
        expected_revision=saved["revision"],
    )


def expansion_payload(modules, saved):
    state, _, _ = modules
    view = state.view(saved)
    return {
        "stage": "expansion_continue",
        "state": view["state"],
        "revision": saved["revision"],
        "facts": {"expansion_observations": view["expansion_observations"]},
    }


def expansion_scope_payload(modules, saved):
    data = expansion_payload(modules, saved)
    data["stage"] = "expansion_scope"
    return data


def expansion_scope_plan(repo, *, path="audit_tool_gap", extra_gap=False):
    def file_source(relative, content):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return {"path": relative, "digest": hashlib.sha256(target.read_bytes()).hexdigest(), "kind": "file"}

    task = {
        "id": "cover-refund",
        "scenario": "A customer requests a partial refund for an eligible order",
        "expected_outcome": "Refund the approved amount and preserve the remaining order balance",
        "grading": "Check the persisted refund amount and order balance",
        "validation": "Positive, no-action, and wrong-amount controls",
        "example_ids": ["partial-refund"],
        "attempts": 2,
        "unresolved_inputs": [],
        "recommendation_id": "R1" if path == "audit_tool_gap" else None,
        "target_tool": "refund" if path == "audit_tool_gap" else None,
    }
    plan = {
        "provider": "gym",
        "tasks": [task],
        "selected_task_ids": [task["id"]],
        "sources": [],
        "coverage_report": None,
        "deferred": [],
        "exclusions": ["Changes to existing cases or graders"],
        "limits": {
            "max_construction_attempts": 4,
            "max_minutes": 20,
            "max_cost_usd": 3,
            "source": "User requested at most twenty minutes and three dollars for construction",
            "unresolved_inputs": [],
        },
        "unresolved_inputs": [],
        "source": "User selected the partial-refund scenario for the supported creation path",
    }
    handoff = None
    if path == "audit_tool_gap":
        if extra_gap:
            plan["tasks"].append(
                {
                    **copy.deepcopy(task),
                    "id": "cover-cancel-order",
                    "target_tool": "cancel_order",
                    "scenario": "A customer cancels an eligible pending order",
                    "recommendation_id": "R3",
                    "expected_outcome": "Cancel the order and release its reserved inventory",
                    "grading": "Check the persisted order status and released inventory",
                    "validation": "Positive, no-action, and wrong-order controls",
                    "example_ids": ["pending-order"],
                }
            )
            plan["selected_task_ids"].append("cover-cancel-order")
        report = {
            "covered": ["lookup_order"],
            "uncovered": [task["target_tool"] for task in plan["tasks"]],
            "uncovered_items": [
                {
                    "name": task["target_tool"],
                    "kind": "tool",
                    "reason": "not_covered_by_any_input_report",
                    "description": task["scenario"],
                    "generation": {
                        "focus": task["expected_outcome"],
                        "needed_tools": [task["target_tool"]],
                        "evidence_required": [{"kind": "tool_call", "tool": task["target_tool"]}],
                    },
                }
                for task in plan["tasks"]
            ],
            "input_reports": [
                {
                    "path": ".eval-author/measurements/order-lookup/coverage.json",
                    "method": "tool_calls",
                    "item_kind": "tool",
                    "item_kind_count": len(plan["tasks"]) + 1,
                    "subject": {"run_id": "lookup-1", "evidence_class": "agent_under_test"},
                    "covered": ["lookup_order"],
                    "covered_count": 1,
                }
            ],
        }
        recommendations = [
            {"id": task["recommendation_id"], "action": "new_task", "items": [task["target_tool"]]}
            for task in plan["tasks"]
        ]
        recommendations.insert(1, {"id": "R2", "action": "repair_verifier", "items": ["order_balance"]})
        coverage = file_source(".eval-author/.working/audit/coverage.json", json.dumps(report) + "\n")
        findings = file_source(
            ".eval-author/audit-coverage-report.md",
            "# Audit findings\n\nR1: Add partial refund coverage. R2: Repair the existing order grader.\n"
            + ("R3: Add order cancellation coverage.\n" if extra_gap else ""),
        )
        content = file_source(
            ".eval-author/.working/audit/report-content.json",
            json.dumps({"recommendations": recommendations}) + "\n",
        )
        handoff = {
            "recommendation_ids": [row["id"] for row in recommendations],
            "report": {key: findings[key] for key in ("path", "digest")},
            "content": {key: content[key] for key in ("path", "digest")},
            "source": "User selected R1, R2, and R3 in the reviewed audit report"
            if extra_gap
            else "User selected R1 and R2 in the reviewed audit report",
        }
        plan["sources"] = [coverage]
        plan["coverage_report"] = coverage["path"]
        plan["deferred"] = [
            {
                "id": "R2",
                "improvement": "Repair the existing order grader",
                "reason": "Applying this grader repair inside Eval Author is not yet supported",
            }
        ]
    elif path == "recorded_interaction":
        plan["sources"] = [file_source("traces/refund.json", json.dumps({"trace_id": "refund-1", "steps": []}) + "\n")]
        plan["source"] = "User selected traces/refund.json for an experimental private task candidate"
    else:
        pattern = file_source(".eval-author/tasks/account-lookup/README.md", "# Account lookup starter task\n")
        plan["sources"] = [
            {"path": str((repo / pattern["path"]).parent.relative_to(repo)), "digest": None, "kind": "task"}
        ]
    return plan, handoff


def expansion_scope_start(
    modules,
    repo,
    *,
    path="audit_tool_gap",
    question="Should I create this task within these construction limits?",
    extra_gap=False,
):
    state, expansion, _ = modules
    setup_ready(state, repo, journey="expand")
    saved = state.load_state(repo) if path == "recorded_interaction" else ethos_ready(state, repo)
    plan, handoff = expansion_scope_plan(repo, path=path, extra_gap=extra_gap)
    if path == "starter_suite":
        evidence = repo / ".eval-author/.working/starter-review.json"
        evidence.write_text(json.dumps({"controls": "passed", "user_review": "accepted"}) + "\n")
        saved = state.record_task(
            repo,
            task=plan["sources"][0]["path"],
            status="accepted",
            source="User accepted the earlier starter task after its authoring workflow review",
            evidence=[str(evidence.relative_to(repo))],
        )
        plan["sources"][0]["digest"] = state.task_digest(repo, plan["sources"][0]["path"])
    if handoff is not None:
        audit = importlib.import_module("audit_state")
        lifecycle = audit.new_record()
        sources = {
            "suites": (".eval-author/.working/audit/source-suite", "cases.json", '{"cases": ["lookup-order"]}\n'),
            "traces": (
                ".eval-author/.working/audit/source-runs",
                "lookup.json",
                '{"run_id": "lookup-1", "tool_calls": ["lookup_order"]}\n',
            ),
        }
        for kind, (root, name, content) in sources.items():
            source = repo / root / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(content)
            lifecycle[kind] = {
                "status": "selected",
                "paths": [root],
                "candidates": [],
                "exclusions": [],
                "source": f"User selected the audit {kind}",
            }
            lifecycle["suite_files" if kind == "suites" else "trace_files"] = [str(source.relative_to(repo))]
        specification = repo / ".eval-author/audit.md"
        specification.write_text("# Audit checks\n\nCheck lookup_order and refund tool evidence.\n")
        lifecycle["specification"] = str(specification.relative_to(repo))
        saved["scopes"]["audit"] = {"lifecycle": lifecycle}
        audit_progress = audit.progress(repo, saved)
        assert audit_progress is not None
        checks = audit_progress["checks_digest"]
        lifecycle["checks_approval"] = {"digest": checks, "source": "User approved the selected audit checks"}
        lifecycle["measurement"] = {
            "checks_digest": checks,
            "status": "complete",
            "evidence": [{key: plan["sources"][0][key] for key in ("path", "digest")}],
            "source": "Measured the selected synthetic agent trace against the approved checks",
        }
        grader_evidence = repo / ".eval-author/.working/audit/grader-screen.json"
        grader_evidence.write_text('{"existing_order_grader": "false acceptance observed"}\n')
        grader_ref = {
            "path": str(grader_evidence.relative_to(repo)),
            "digest": hashlib.sha256(grader_evidence.read_bytes()).hexdigest(),
            "kind": "file",
        }
        lifecycle["findings"] = {
            "report": handoff["report"],
            "content": handoff["content"],
            "inputs": [*copy.deepcopy(plan["sources"]), grader_ref],
            "source": "Presented the measured audit findings",
        }
        audit_progress = audit.progress(repo, saved)
        assert audit_progress is not None
        findings_digest = audit_progress["findings_digest"]
        lifecycle["findings_review"] = {"digest": findings_digest, "source": "User reviewed the audit findings"}
        lifecycle["next_action"] = {
            "kind": "improve",
            "findings_digest": findings_digest,
            "recommendation_ids": handoff["recommendation_ids"],
            "source": handoff["source"],
        }
        audit.retain_handoff({"scopes": {}}, saved)
        state.save(repo, saved)
    value = expansion.new_scope(
        saved,
        path=path,
        request={
            "outcome": "Add coverage for partial refunds",
            "source": "User requested the selected supported addition",
        },
        plan=copy.deepcopy(plan),
        question=question,
        source="User selected this supported suite improvement",
    )
    return state.update(
        repo,
        {"scopes": {"expand": {"lifecycle": value}}, "pending_question": question},
        expected_revision=saved["revision"],
    )


def expansion_scope_replace(modules, repo, saved, value, **patch):
    state, _, _ = modules
    return state.update(
        repo,
        {
            "scopes": {"expand": {"lifecycle": value}},
            "pending_question": value["pending"]["question"] if value["pending"] else None,
            **patch,
        },
        expected_revision=saved["revision"],
    )


def expansion_scope_present(modules, repo, saved):
    _, expansion, _ = modules
    value = copy.deepcopy(expansion.record(saved))
    value["presented"] = {
        "digest": expansion.scope_digest(saved),
        "source": "Displayed the current scope table and limits",
    }
    return expansion_scope_replace(modules, repo, saved, value)


def expansion_scope_decide(modules, repo, saved, kind="approved"):
    _, expansion, _ = modules
    value = copy.deepcopy(expansion.record(saved))
    value["decision"] = {"digest": expansion.scope_digest(saved), "kind": kind, "source": f"User {kind} this scope"}
    value["pending"] = (
        {"question": "What would you like to change in this scope?", "source": "User requested a scope revision"}
        if kind == "revise"
        else None
    )
    return expansion_scope_replace(modules, repo, saved, value)


def expansion_scope_approve(modules, repo, saved):
    return expansion_scope_decide(modules, repo, expansion_scope_present(modules, repo, saved))


def expansion_scope_progress(modules, repo, saved):
    return modules[1].derive(saved, modules[1].observe(repo, saved))
