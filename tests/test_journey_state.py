# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise prerequisite evidence, user decisions, content binding, and safe resumption."""

import copy
import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"


@pytest.fixture
def state(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("journey_state")


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


@pytest.mark.parametrize("provider", ["gym", "harbor"])
@pytest.mark.parametrize(
    "journey,label",
    [
        ("create", "Build, run, and review your first evaluations"),
        ("audit", "Review evaluation coverage, identify gaps, and recommend improvements"),
        ("expand", "Improve, run, and review your existing evaluations"),
    ],
)
def test_same_three_step_flow_and_execution_credentials_deferred(state, tmp_path, provider, journey, label):
    initial = state.initialize(tmp_path, journey=journey, setup=provider)
    assert len(state.checklist(initial)) == 3
    assert state.checklist(initial)[0]["current"]
    assert state.checklist(initial)[2]["label"] == label
    ready = setup_ready(state, tmp_path, journey=journey, setup=provider)
    assert state.checklist(ready)[0]["complete"]
    assert state.checklist(ready)[1]["current"]
    assert not (state.DEFERRED & ready["prerequisites"].keys())
    ready = ethos_ready(state, tmp_path)
    assert state.checklist(ready)[2]["current"]
    assert state.view(ready)["confirmation_is_runtime_evidence"] is False
    assert not state.checklist(ready)[2]["complete"]


def test_downstream_resume_preserves_progress_and_audit_switch_reopens_applicability(state, tmp_path):
    setup_ready(state, tmp_path)
    original = ethos_ready(state, tmp_path)
    state.update(
        tmp_path,
        {
            "scopes": {"create": {"cases": ["refund"], "confirmed": True}},
            "journey_status": {"create": "in_progress"},
            "pending_question": "Which case first?",
            "next_handoff": "eval-author-first-eval",
        },
    )
    ongoing = state.initialize(tmp_path, agent="assistant", journey="create", setup="gym")
    assert ongoing["ethos"] == original["ethos"]
    assert ongoing["scopes"]["create"]["confirmed"]
    assert ongoing["journey_status"]["create"] == "in_progress"
    assert ongoing["pending_question"] == "Which case first?"
    assert ongoing["next_handoff"] == "eval-author-first-eval"
    resumed = state.initialize(tmp_path, agent="assistant", journey="audit", setup="gym")
    for key, answer in original["prerequisites"].items():
        assert resumed["prerequisites"][key]["source"] == answer["source"]
        assert resumed["prerequisites"][key]["status"] == (
            "unknown" if key in state.AUDIT_DEPENDENT else answer["status"]
        )
    assert resumed["ethos"] == original["ethos"]
    assert resumed["scopes"]["create"]["cases"] == ["refund"]
    assert resumed["scopes"]["create"]["confirmed"]
    assert resumed["journey_status"]["create"] == "in_progress"
    assert resumed["pending_question"] is None
    assert state.checklist(resumed)[2]["label"] == (
        "Review evaluation coverage, identify gaps, and recommend improvements"
    )


def audit_ready(state, repo, *, setup="gym"):
    setup_ready(state, repo, journey="audit", setup=setup)
    return state.update(
        repo,
        {
            "prerequisites": {
                "python": {
                    "status": "verified",
                    "source": "Python 3.11 with PyYAML 6.0 and jsonschema 4.0 for audit generation and validation",
                },
                "data": {
                    "status": "confirmed",
                    "source": "User confirms traces from completed evaluation runs are available",
                },
                **{
                    key: {"status": "not_required", "source": "Local audit reads definitions and saved evidence"}
                    for key in {"runtime", "environment", "services", *state.DEFERRED}
                },
            }
        },
    )


@pytest.mark.parametrize("provider", ["gym", "harbor"])
def test_audit_ready_without_runners_containers_or_execution_credentials(state, tmp_path, provider):
    ready = audit_ready(state, tmp_path, setup=provider)
    assert state.checklist(ready)[0]["complete"]
    assert state.checklist(ready)[1]["current"]
    requirements = state.view(ready)["runtime_requirements"]
    assert requirements == {
        "runtime": None,
        "python": ">=3.11",
        "packages": {"PyYAML": ">=6.0", "jsonschema": ">=4.0"},
    }
    assert state.load_state(tmp_path) == ready


@pytest.mark.parametrize("status", [None, "unknown", "missing", "not_required"])
def test_unresolved_or_legacy_exempt_traces_cannot_establish_audit_readiness(state, tmp_path, status):
    audit_ready(state, tmp_path)
    ethos_ready(state, tmp_path)
    legacy = state.update(tmp_path, {"journey_status": {"audit": "complete"}})
    if status is None:
        del legacy["prerequisites"]["data"]
    else:
        legacy["prerequisites"]["data"] = {"status": status, "source": "Prior audit prerequisite record"}
    state.save(tmp_path, legacy)
    before = state.state_path(tmp_path).read_bytes()
    loaded = state.load_state(tmp_path)
    assert loaded["schema_version"] == 1
    assert loaded["prerequisites"] == legacy["prerequisites"]
    assert not state.checklist(loaded)[0]["complete"]
    assert not state.checklist(loaded)[2]["complete"]
    assert state.checklist(loaded)[0]["current"]
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("status", ["confirmed", "verified"])
def test_trace_availability_completes_audit_prerequisites_without_selecting_scope_or_completing_audit(
    state, tmp_path, status
):
    audit_ready(state, tmp_path)
    ethos_ready(state, tmp_path)
    state.update(
        tmp_path,
        {
            "prerequisites": {"data": {"status": "unknown", "source": "Trace availability unresolved"}},
            "blockers": [{"id": "data", "phase": "setup", "message": "Completed-run traces are needed"}],
            "pending_question": "Are traces from completed evaluation runs available?",
            "scopes": {"audit": {"confirmed": False}},
        },
    )
    resolved = state.update(
        tmp_path,
        {
            "prerequisites": {"data": {"status": status, "source": "Completed-run traces are available"}},
            "blockers": [],
            "pending_question": None,
        },
    )
    assert state.checklist(resolved)[0]["complete"]
    assert resolved["scopes"] == {"audit": {"confirmed": False}}
    assert not resolved["journey_status"]
    assert not state.checklist(resolved)[2]["complete"]


@pytest.mark.parametrize("journey", ["create", "expand"])
def test_trace_availability_blocker_survives_audit_resume_and_is_scoped_when_switching(state, tmp_path, journey):
    audit_ready(state, tmp_path)
    blocked = state.update(
        tmp_path,
        {
            "prerequisites": {"data": {"status": "unknown", "source": "Trace availability unresolved"}},
            "blockers": [{"id": "data", "phase": "setup", "message": "Completed-run traces are needed"}],
            "pending_question": "Are traces from completed evaluation runs available?",
            "next_handoff": "eval-author",
        },
    )
    resumed = state.initialize(tmp_path, journey="audit")
    assert resumed["prerequisites"] == blocked["prerequisites"]
    assert resumed["blockers"] == blocked["blockers"]
    assert resumed["pending_question"] == blocked["pending_question"]
    assert resumed["next_handoff"] == blocked["next_handoff"]
    assert not state.checklist(resumed)[0]["complete"]
    assert state.checklist(resumed)[0]["blocked"]
    changed = state.initialize(tmp_path, journey=journey)
    assert changed["blockers"] == [{**blocked["blockers"][0], "phase": "audit"}]
    assert changed["prerequisites"]["data"]["source"] == blocked["prerequisites"]["data"]["source"]
    assert changed["pending_question"] is None
    assert not state.checklist(changed)[0]["blocked"]
    returned = state.initialize(tmp_path, journey="audit")
    assert returned["prerequisites"]["data"]["status"] == "unknown"
    assert not state.checklist(returned)[0]["complete"]
    assert state.checklist(returned)[2]["blocked"]


@pytest.mark.parametrize("journey", ["create", "expand"])
def test_authoring_can_complete_prerequisites_without_prior_traces(state, tmp_path, journey):
    setup_ready(state, tmp_path, journey=journey)
    ready = state.update(
        tmp_path,
        {"prerequisites": {"data": {"status": "not_required", "source": "No prior traces are needed for authoring"}}},
    )
    assert state.checklist(ready)[0]["complete"]
    resumed = state.initialize(tmp_path, journey=journey)
    assert state.checklist(resumed)[0]["complete"]
    assert resumed["prerequisites"] == ready["prerequisites"]


@pytest.mark.parametrize("journey", ["create", "expand"])
@pytest.mark.parametrize("provider", ["gym", "harbor"])
@pytest.mark.parametrize("status", [None, "unknown", "missing"])
def test_later_authoring_dependencies_do_not_delay_ethos(state, tmp_path, journey, provider, status):
    state.initialize(tmp_path, agent="assistant", journey=journey, setup=provider)
    prerequisites = {
        key: {"status": "verified", "source": "Current prerequisite check passed"}
        for key in ("code_access", "file_writes", "command_execution", "skills", "ethos_skills", "python", "runtime")
    }
    if status is not None:
        prerequisites.update(
            {
                key: {"status": status, "source": "Needed before dependent execution, not before Ethos"}
                for key in ("environment", "services", "data", "agent_connection", "model_credentials")
            }
        )
    ready = state.update(tmp_path, {"prerequisites": prerequisites})
    resumed = state.initialize(tmp_path, journey=journey, setup=provider)
    assert resumed["prerequisites"] == ready["prerequisites"]
    assert state.checklist(resumed)[0]["complete"]
    assert state.checklist(resumed)[1]["current"]
    assert not state.checklist(resumed)[2]["complete"]
    assert state.view(resumed)["confirmation_is_runtime_evidence"] is False


@pytest.mark.parametrize("provider", [None, "gym", "harbor"])
@pytest.mark.parametrize("status", [None, "unknown", "missing"])
def test_audit_readiness_uses_audit_requirements_and_actual_framework(state, tmp_path, provider, status):
    state.initialize(tmp_path, agent="assistant", journey="audit", setup=provider)
    prerequisites = {
        key: {"status": "verified", "source": "Current access, required skills, and audit tooling checks passed"}
        for key in ("code_access", "file_writes", "command_execution", "skills", "ethos_skills", "python")
    }
    prerequisites["data"] = {"status": "confirmed", "source": "User confirms completed evaluation traces exist"}
    if status is not None:
        prerequisites.update(
            {
                key: {"status": status, "source": "Historical authoring dependency; not used for this audit"}
                for key in ("runtime", "environment", "services", "agent_connection", "model_credentials")
            }
        )
    framework = {"name": "NAT/DeepEval", "source": "Existing evaluation documentation"}
    ready = state.update(
        tmp_path, {"prerequisites": prerequisites, "scopes": {"audit": {"framework": framework, "confirmed": False}}}
    )
    resumed = state.initialize(tmp_path, journey="audit")
    assert resumed["setup"] == provider
    assert resumed["prerequisites"] == ready["prerequisites"]
    assert resumed["scopes"]["audit"] == {"framework": framework, "confirmed": False}
    assert state.checklist(resumed)[0]["complete"]
    assert state.checklist(resumed)[1]["current"]
    assert not state.checklist(resumed)[2]["complete"]
    assert state.view(resumed)["runtime_requirements"]["runtime"] is None


@pytest.mark.parametrize("journey", ["create", "expand"])
@pytest.mark.parametrize("required", ["runtime", "python"])
@pytest.mark.parametrize("status", [None, "unknown", "missing"])
def test_authoring_runtime_and_python_still_block_before_ethos(state, tmp_path, journey, required, status):
    ready = setup_ready(state, tmp_path, journey=journey, setup="harbor")
    if status is None:
        del ready["prerequisites"][required]
    else:
        ready["prerequisites"][required] = {"status": status, "source": "Selected authoring tooling unresolved"}
    state.save(tmp_path, ready)
    loaded = state.load_state(tmp_path)
    assert not state.checklist(loaded)[0]["complete"]
    assert state.checklist(loaded)[0]["current"]
    assert not state.checklist(loaded)[1]["current"]


@pytest.mark.parametrize("journey", ["create", "expand"])
def test_audit_without_authoring_provider_cannot_establish_authoring_readiness(state, tmp_path, journey):
    audit_ready(state, tmp_path, setup=None)
    changed = state.initialize(tmp_path, journey=journey)
    assert changed["setup"] is None
    assert not state.checklist(changed)[0]["complete"]
    reassessed = state.update(
        tmp_path,
        {
            "prerequisites": {
                key: {"status": "verified", "source": "Authoring check passed"}
                for key in ("runtime", "python", "skills")
            }
        },
    )
    assert reassessed["setup"] is None
    assert not state.checklist(reassessed)[0]["complete"]
    ready = setup_ready(state, tmp_path, journey=journey, setup="harbor")
    assert state.checklist(ready)[0]["complete"]


@pytest.mark.parametrize("provider", ["gym", "harbor"])
@pytest.mark.parametrize("journey", ["create", "expand"])
@pytest.mark.parametrize("audit_status", ["in_progress", "complete"])
@pytest.mark.parametrize("state_only", [False, True])
@pytest.mark.parametrize("separate_selection", [False, True])
def test_first_authoring_provider_preserves_audit_progress(
    state, tmp_path, provider, journey, audit_status, state_only, separate_selection
):
    audit_ready(state, tmp_path, setup=None)
    original = state.update(
        tmp_path,
        {
            "scopes": {
                "audit": {
                    "framework": "NAT/DeepEval",
                    "framework_source": "Existing suite documentation",
                    "traces": ["selected.json"],
                    "priorities": ["refund coverage"],
                    "confirmed": True,
                }
            },
            "journey_status": {"audit": audit_status},
            "pending_question": "Review the selected audit findings?",
            "next_handoff": "eval-author-audit",
            "blockers": (
                [{"id": "selected_source", "phase": "audit", "message": "Review selected-source access"}]
                if audit_status == "in_progress"
                else []
            ),
        },
    )
    if separate_selection:
        state.initialize(tmp_path, journey=journey, state_only=state_only)
    changed = state.initialize(tmp_path, journey=journey, setup=provider, state_only=state_only)
    assert changed["journey"] == journey
    assert changed["setup"] == provider
    for field in ("scopes", "journey_status", "blockers"):
        assert changed[field] == original[field]
    for key, answer in original["prerequisites"].items():
        assert changed["prerequisites"][key] == {
            **answer,
            "status": "unknown" if key in state.AUDIT_DEPENDENT else answer["status"],
        }
    assert changed["pending_question"] is None
    assert changed["next_handoff"] is None
    assert not state.checklist(changed, state_only=state_only)[0]["complete"]
    assert state.load_state(tmp_path, state_only=state_only) == changed
    policy = state.runtime_policy()["providers"][provider]
    assert state.view(changed, state_only=state_only)["runtime_requirements"] == {
        "runtime": policy["supported"]["authoring"],
        "python": policy["python"],
    }
    reassessed = state.update(
        tmp_path,
        {
            "prerequisites": {
                key: {"status": "verified", "source": "Current authoring checks passed"}
                for key in ("runtime", "python", "skills")
            }
        },
        state_only=state_only,
    )
    assert state.checklist(reassessed, state_only=state_only)[0]["complete"]
    for field in ("scopes", "journey_status", "blockers"):
        assert reassessed[field] == original[field]


@pytest.mark.parametrize("journey", ["create", "audit", "expand"])
@pytest.mark.parametrize(
    "required", ["code_access", "file_writes", "command_execution", "skills", "ethos_skills", "python"]
)
def test_unresolved_common_requirements_still_prevent_setup_completion(state, tmp_path, journey, required):
    setup_ready(state, tmp_path, journey=journey)
    pending = state.update(
        tmp_path, {"prerequisites": {required: {"status": "unknown", "source": "Current prerequisite unresolved"}}}
    )
    assert not state.checklist(pending)[0]["complete"]
    assert state.checklist(pending)[0]["current"]


@pytest.mark.parametrize("journey,requirement", [("create", "data"), ("expand", "environment"), ("audit", "services")])
def test_explicit_setup_blocker_is_not_dismissed_as_a_later_dependency(state, tmp_path, journey, requirement):
    setup_ready(state, tmp_path, journey=journey)
    blocked = state.update(
        tmp_path,
        {
            "prerequisites": {requirement: {"status": "missing", "source": "Required to read agent intent sources"}},
            "blockers": [{"id": requirement, "phase": "setup", "message": "Cannot access the next Ethos input"}],
        },
    )
    assert not state.checklist(blocked)[0]["complete"]
    assert state.checklist(blocked)[0]["blocked"]
    assert not any(row["current"] for row in state.checklist(blocked))


@pytest.mark.parametrize(
    "required", ["code_access", "file_writes", "command_execution", "skills", "ethos_skills", "python"]
)
def test_audit_still_requires_access_skills_and_python(state, tmp_path, required):
    audit_ready(state, tmp_path)
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match=f"{required} is required"):
        state.update(tmp_path, {"prerequisites": {required: {"status": "not_required", "source": "Skip checks"}}})
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("journey", [None, "create", "expand"])
def test_runtime_exemption_is_only_valid_for_audit(state, tmp_path, journey):
    state.initialize(tmp_path, journey=journey, setup="harbor")
    with pytest.raises(ValueError, match="runtime is required"):
        state.update(tmp_path, {"prerequisites": {"runtime": {"status": "not_required", "source": "No runner"}}})


@pytest.mark.parametrize("provider", ["gym", "harbor"])
@pytest.mark.parametrize("journey", ["create", "expand"])
def test_audit_exemptions_and_weaker_python_cannot_establish_authoring_readiness(state, tmp_path, provider, journey):
    audit_ready(state, tmp_path, setup=provider)
    ethos_ready(state, tmp_path)
    original = state.update(
        tmp_path,
        {
            "scopes": {"audit": {"traces": ["selected.json"], "confirmed": True}},
            "journey_status": {"audit": "complete"},
        },
    )
    changed = state.initialize(tmp_path, journey=journey)
    assert changed["schema_version"] == 1
    for key, answer in original["prerequisites"].items():
        assert changed["prerequisites"][key] == {
            "status": "unknown" if key in state.AUDIT_DEPENDENT else answer["status"],
            "source": answer["source"],
        }
    assert not state.checklist(changed)[0]["complete"]
    assert changed["ethos"] == original["ethos"]
    assert changed["scopes"] == original["scopes"]
    assert changed["journey_status"] == original["journey_status"]
    policy = state.runtime_policy()["providers"][provider]
    assert state.view(changed)["runtime_requirements"] == {
        "runtime": policy["supported"]["authoring"],
        "python": policy["python"],
    }
    assert state.load_state(tmp_path) == changed


@pytest.mark.parametrize("before,after", [("audit", "audit"), ("create", "expand"), ("expand", "create")])
def test_compatible_journey_resume_preserves_prerequisite_evidence(state, tmp_path, before, after):
    if before == "audit":
        audit_ready(state, tmp_path)
    else:
        setup_ready(state, tmp_path, journey=before)
    original = state.update(
        tmp_path,
        {
            "pending_question": "Current question",
            "next_handoff": "current-flow",
            "journey_status": {before: "in_progress"},
        },
    )
    resumed = state.initialize(tmp_path, journey=after)
    assert resumed["prerequisites"] == original["prerequisites"]
    assert resumed["journey_status"] == original["journey_status"]
    assert state.checklist(resumed)[0]["complete"]
    assert resumed["pending_question"] == ("Current question" if before == after else None)
    assert resumed["next_handoff"] == ("current-flow" if before == after else None)


@pytest.mark.parametrize("journey", ["create", "expand"])
def test_authoring_problems_are_reassessed_for_audit_without_discarding_evidence(state, tmp_path, journey):
    setup_ready(state, tmp_path, journey=journey, setup="harbor")
    original = state.update(
        tmp_path,
        {
            "prerequisites": {
                "runtime": {"status": "missing", "source": "Harbor CLI was not found"},
                "environment": {"status": "missing", "source": "Docker daemon was unavailable"},
                "model_credentials": {"status": "missing", "source": "Required credential was unavailable"},
            },
            "pending_question": "Provide an authoring runtime?",
            "next_handoff": "runtime-setup",
            "blockers": [
                {"id": "runtime", "phase": "setup", "message": "Harbor CLI was not found"},
                {"id": "environment", "phase": "setup", "message": "Docker daemon was unavailable"},
                {"id": "model_credentials", "phase": "setup", "message": "Required credential was unavailable"},
            ],
        },
    )
    changed = state.initialize(tmp_path, journey="audit")
    assert changed["pending_question"] is None
    assert changed["next_handoff"] is None
    for key in ("runtime", "environment", "model_credentials"):
        assert changed["prerequisites"][key]["status"] == "unknown"
        assert changed["prerequisites"][key]["source"] == original["prerequisites"][key]["source"]
    assert changed["prerequisites"]["code_access"] == original["prerequisites"]["code_access"]
    assert changed["prerequisites"]["ethos_skills"] == original["prerequisites"]["ethos_skills"]
    assert changed["blockers"] == [{**blocker, "phase": journey} for blocker in original["blockers"]]
    assert not state.checklist(changed)[0]["blocked"]
    assert not state.checklist(changed)[2]["blocked"]


def test_flow_switch_preserves_common_and_unclassified_legacy_blockers_for_review(state, tmp_path):
    audit_ready(state, tmp_path)
    original = state.update(
        tmp_path,
        {
            "blockers": [
                {"id": "python", "phase": "setup", "message": "Audit Python packages unavailable"},
                {"id": "skills", "phase": "setup", "message": "Audit skill unavailable"},
                {"id": "code_access", "phase": "setup", "message": "Agent source inaccessible"},
                {"id": "ethos_skills", "phase": "setup", "message": "Ethos skills unavailable"},
                {"id": "legacy-setup", "phase": "setup", "message": "Unclassified setup problem"},
                {"id": "review-intent", "phase": "ethos", "message": "Intent still needs confirmation"},
                {"id": "select-traces", "phase": "audit", "message": "Trace selection unresolved"},
            ]
        },
    )
    changed = state.initialize(tmp_path, journey="create")
    assert changed["blockers"][:2] == [{**blocker, "phase": "audit"} for blocker in original["blockers"][:2]]
    assert changed["blockers"][2:] == original["blockers"][2:]
    assert state.checklist(changed)[0]["blocked"]
    assert state.checklist(changed)[1]["blocked"]
    assert not state.checklist(changed)[2]["blocked"]


def test_returning_to_authoring_reconciles_preserved_blockers_with_new_checks(state, tmp_path):
    setup_ready(state, tmp_path, setup="harbor")
    state.update(
        tmp_path,
        {
            "prerequisites": {"runtime": {"status": "missing", "source": "Harbor CLI was not found"}},
            "blockers": [{"id": "runtime", "phase": "setup", "message": "Harbor CLI was not found"}],
        },
    )
    audited = audit_ready(state, tmp_path, setup="harbor")
    assert state.checklist(audited)[0]["complete"]
    assert not state.checklist(audited)[2]["blocked"]
    returned = state.initialize(tmp_path, journey="create")
    assert returned["prerequisites"]["runtime"]["status"] == "unknown"
    assert not state.checklist(returned)[0]["complete"]
    assert returned["blockers"] == [{"id": "runtime", "phase": "create", "message": "Harbor CLI was not found"}]
    assert state.checklist(returned)[2]["blocked"]
    reassessed = state.update(
        tmp_path,
        {
            "prerequisites": {
                key: {"status": "verified", "source": "Current authoring checks passed"} for key in state.PREREQUISITES
            },
            "blockers": [],
        },
    )
    assert state.checklist(reassessed)[0]["complete"]
    assert not state.checklist(reassessed)[2]["blocked"]


def test_ethos_completion_pause_survives_resume_and_continuation(state, tmp_path):
    setup_ready(state, tmp_path)
    confirmed = ethos_ready(state, tmp_path)
    question = "We’re ready to build your evaluation suite. Ready to continue?"
    state.update(tmp_path, {"pending_question": question, "next_handoff": "eval-author-first-eval"})
    resumed = state.initialize(tmp_path, agent="assistant", journey="create", setup="gym")
    assert resumed["ethos"] == confirmed["ethos"]
    assert resumed["pending_question"] == question
    assert resumed["next_handoff"] == "eval-author-first-eval"
    assert [row["complete"] for row in state.checklist(resumed)] == [True, True, False]
    assert state.checklist(resumed)[2]["current"]
    continued = state.update(tmp_path, {"pending_question": None, "journey_status": {"create": "in_progress"}})
    assert continued["ethos"] == confirmed["ethos"]
    assert state.load_state(tmp_path)["pending_question"] is None
    assert not state.checklist(continued)[2]["complete"]


@pytest.mark.parametrize("journey", ["create", "audit", "expand"])
def test_existing_ethos_review_requires_current_confirmation(state, tmp_path, journey):
    setup_ready(state, tmp_path, journey=journey)
    previous = ethos_ready(state, tmp_path)
    source_findings = [{"path": "README.md", "summary": "Account support agent"}]
    prior_answers = [
        {"topic": "purpose", "question": "What outcome?", "answer": "Account recovery", "source": "old reply"}
    ]
    state.update(tmp_path, {"ethos": {"source_findings": source_findings, "prior_answers": prior_answers}})
    question = (
        "Is this the right ETHOS.md for assistant, and does it accurately describe how the agent should behave today?"
    )
    state.update(
        tmp_path,
        {"ethos": {"stage": "review"}, "pending_question": question, "next_handoff": "ethos-orchestrate"},
    )
    pending = state.initialize(tmp_path, agent="assistant", journey=journey, setup="gym")
    for field in ("confirmed_digest", "confirmation_source", "validation"):
        assert pending["ethos"][field] == previous["ethos"][field]
    assert pending["ethos"]["stage"] == "review"
    assert pending["ethos"]["source_findings"] == source_findings
    assert pending["ethos"]["prior_answers"] == prior_answers
    assert pending["prerequisites"] == previous["prerequisites"]
    assert pending["pending_question"] == question
    assert pending["next_handoff"] == "ethos-orchestrate"
    assert [row["complete"] for row in state.checklist(pending)] == [True, False, False]
    assert state.checklist(pending)[1]["current"]

    confirmed = state.confirm_ethos(
        tmp_path,
        reviewed_digest=state.ethos_digest(tmp_path),
        source="current conversation: user confirms document identity and intent",
        validation="ethos-write parser passed",
    )
    assert confirmed["ethos"]["confirmed_digest"] == previous["ethos"]["confirmed_digest"]
    assert confirmed["ethos"]["confirmation_source"] != previous["ethos"]["confirmation_source"]
    assert [row["complete"] for row in state.checklist(confirmed)] == [True, True, False]
    assert state.load_state(tmp_path)["ethos"] == confirmed["ethos"]


@pytest.mark.parametrize("provider", ["gym", "harbor"])
def test_fresh_ethos_review_preserves_completed_scope_after_goal_switch(state, tmp_path, provider):
    setup_ready(state, tmp_path, setup=provider)
    previous = ethos_ready(state, tmp_path)
    scope = {"cases": ["refund"], "confirmed": True, "needs_review": False}
    completed = state.update(tmp_path, {"scopes": {"create": scope}, "journey_status": {"create": "complete"}})
    state.initialize(tmp_path, journey="expand")
    question = "Is this still the right ETHOS.md and intended behavior?"
    pending = state.update(
        tmp_path,
        {"ethos": {"stage": "review"}, "pending_question": question, "next_handoff": "ethos-orchestrate"},
    )
    resumed = state.initialize(tmp_path, journey="expand")
    assert state.load_state(tmp_path) == resumed
    assert resumed["ethos"] == pending["ethos"]
    assert resumed["scopes"] == completed["scopes"]
    assert resumed["journey_status"] == completed["journey_status"]
    assert resumed["ethos"]["confirmed_digest"] == previous["ethos"]["confirmed_digest"]
    assert [row["complete"] for row in state.checklist(resumed)] == [True, False, False]
    assert state.checklist(resumed)[1]["current"]
    assert resumed["pending_question"] == question
    assert resumed["next_handoff"] == "ethos-orchestrate"

    confirmed = state.confirm_ethos(
        tmp_path,
        reviewed_digest=state.ethos_digest(tmp_path),
        source="Current reply confirms unchanged content",
        validation="Current saved-file validation passed",
    )
    assert confirmed["scopes"] == completed["scopes"]
    assert confirmed["journey_status"] == completed["journey_status"]
    assert confirmed["ethos"]["confirmation_source"] != previous["ethos"]["confirmation_source"]
    assert state.checklist(confirmed)[2]["current"]
    returned = state.initialize(tmp_path, journey="create")
    assert all(row["complete"] for row in state.checklist(returned))


@pytest.mark.parametrize("change", ["edit", "delete"])
@pytest.mark.parametrize("revision_stage", [None, "explore", "interview", "draft"])
def test_saved_ethos_review_does_not_require_its_old_authoring_draft(state, tmp_path, change, revision_stage):
    setup_ready(state, tmp_path)
    draft = tmp_path / ".eval-author/.working/draft.md"
    draft.write_text("# Confirmed agent intent\n")
    state.update(
        tmp_path,
        {
            "ethos": {
                "stage": "write",
                "draft": {"path": draft.relative_to(tmp_path).as_posix(), "digest": state.digest(draft.read_bytes())},
                "draft_confirmed": True,
                "draft_source": "Approved authoring draft",
            }
        },
    )
    previous = ethos_ready(state, tmp_path)
    completed = state.update(
        tmp_path,
        {"scopes": {"create": {"cases": ["refund"], "confirmed": True}}, "journey_status": {"create": "complete"}},
    )
    if change == "edit":
        draft.write_text("Obsolete working copy")
    else:
        draft.unlink()

    state.update(tmp_path, {"ethos": {"stage": "review"}})
    resumed = state.initialize(tmp_path, journey="expand")
    assert resumed["ethos"]["stage"] == "review"
    assert resumed["ethos"]["confirmed_digest"] == previous["ethos"]["confirmed_digest"]
    assert resumed["scopes"] == completed["scopes"]
    assert resumed["journey_status"] == completed["journey_status"]
    assert state.load_state(tmp_path) == resumed
    if revision_stage:
        rejected = state.update(tmp_path, {"ethos": {"stage": revision_stage}})
        assert rejected["ethos"]["confirmed_digest"] is None
        assert not rejected["ethos"]["draft_confirmed"]
        assert rejected["ethos"]["draft_source"] is None
        assert rejected["scopes"]["create"] == {"cases": ["refund"], "confirmed": False, "needs_review": True}
        assert not rejected["journey_status"]
        assert state.load_state(tmp_path) == rejected
        return
    confirmed = state.confirm_ethos(
        tmp_path,
        reviewed_digest=state.ethos_digest(tmp_path),
        source="Reviewed unchanged saved document",
        validation="Saved document validation passed",
    )
    assert confirmed["scopes"] == completed["scopes"]
    assert confirmed["journey_status"] == completed["journey_status"]
    assert state.load_state(tmp_path) == confirmed


def test_revised_ethos_requires_and_retains_current_draft_approval(state, tmp_path):
    setup_ready(state, tmp_path)
    ethos_ready(state, tmp_path)
    state.update(
        tmp_path,
        {"scopes": {"create": {"cases": ["refund"], "confirmed": True}}, "journey_status": {"create": "complete"}},
    )
    state.update(tmp_path, {"ethos": {"stage": "review"}})
    draft = tmp_path / ".eval-author/.working/revised.md"
    draft.write_text("Revised intended behavior")
    reference = {"path": draft.relative_to(tmp_path).as_posix(), "digest": state.digest(draft.read_bytes())}
    revised = state.update(
        tmp_path,
        {"ethos": {"stage": "write", "draft": reference, "draft_confirmed": True, "draft_source": "Current reply"}},
    )
    assert revised["ethos"]["confirmed_digest"] is None
    assert revised["ethos"]["draft"] == reference
    assert revised["ethos"]["draft_confirmed"]
    assert revised["ethos"]["draft_source"] == "Current reply"
    assert not revised["scopes"]["create"]["confirmed"]
    assert not revised["journey_status"]
    assert state.load_state(tmp_path) == revised
    draft.write_text("Changed after approval")
    assert not state.load_state(tmp_path)["ethos"]["draft_confirmed"]


@pytest.mark.parametrize("stage", ["explore", "interview", "draft"])
def test_rejected_ethos_intent_invalidates_scope_without_file_changes(state, tmp_path, stage):
    setup_ready(state, tmp_path)
    original = ethos_ready(state, tmp_path)
    state.update(
        tmp_path,
        {
            "scopes": {"create": {"cases": ["refund"], "confirmed": True}},
            "journey_status": {"create": "complete"},
        },
    )
    state.update(tmp_path, {"ethos": {"stage": "review"}})
    rejected = state.update(tmp_path, {"ethos": {"stage": stage}, "pending_question": "Clarify changed intent"})
    assert state.ethos_digest(tmp_path) == original["ethos"]["confirmed_digest"]
    assert rejected["ethos"]["confirmed_digest"] is None
    assert rejected["ethos"]["confirmation_source"] is None
    assert rejected["ethos"]["validation"] is None
    assert rejected["scopes"]["create"] == {"cases": ["refund"], "confirmed": False, "needs_review": True}
    assert not rejected["journey_status"]
    assert not state.checklist(rejected)[1]["complete"]
    assert state.load_state(tmp_path) == rejected


def test_observed_checks_and_user_answers_survive_resume_without_implying_execution(state, tmp_path):
    setup_ready(state, tmp_path, setup="harbor")
    checks = {
        "runtime": {"status": "verified", "source": "harbor --version returned 0.20.0"},
        "python": {"status": "verified", "source": ".venv/bin/python --version returned Python 3.12.9"},
        "environment": {"status": "verified", "source": "docker info returned server information"},
        "services": {"status": "not_required", "source": "Selected task environment uses local fixtures"},
    }
    recorded = state.update(tmp_path, {"prerequisites": checks})
    resumed = state.initialize(tmp_path, agent="assistant", journey="create", setup="harbor")
    assert resumed["schema_version"] == 1
    assert resumed["prerequisites"] == recorded["prerequisites"]
    assert resumed["prerequisites"]["file_writes"] == {"status": "confirmed", "source": "user reply 2"}
    assert state.load_state(tmp_path) == resumed
    rows = state.checklist(resumed)
    assert rows[0]["complete"]
    assert rows[1]["current"] and not rows[1]["complete"]
    assert not rows[2]["complete"]
    assert state.view(resumed)["confirmation_is_runtime_evidence"] is False


@pytest.mark.parametrize("evidence", [{}, {"source": None}, {"source": ""}, {"source": "   "}])
def test_verified_check_requires_source_and_preserves_previous_state(state, tmp_path, evidence):
    state.initialize(tmp_path, journey="create", setup="harbor")
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="prerequisite answer|runtime.source"):
        state.update(tmp_path, {"prerequisites": {"runtime": {"status": "verified", **evidence}}})
    assert state.state_path(tmp_path).read_bytes() == before


def test_provider_change_invalidates_only_provider_answers_and_scope_approval(state, tmp_path):
    setup_ready(state, tmp_path)
    state.update(
        tmp_path,
        {
            "prerequisites": {
                "runtime": {"status": "verified", "source": "NeMo Gym import check succeeded"},
                "python": {"status": "verified", "source": ".venv/bin/python --version returned Python 3.13.14"},
                "skills": {"status": "verified", "source": "Required skill instructions were readable"},
            }
        },
    )
    original = ethos_ready(state, tmp_path)
    state.update(
        tmp_path,
        {"scopes": {"create": {"cases": ["refund"], "confirmed": True}}, "journey_status": {"create": "complete"}},
    )
    changed = state.initialize(tmp_path, setup="harbor")
    assert changed["ethos"] == original["ethos"]
    assert changed["prerequisites"] == {
        key: value for key, value in original["prerequisites"].items() if key not in state.PROVIDER_DEPENDENT
    }
    assert changed["prerequisites"]["skills"]["status"] == "verified"
    assert changed["prerequisites"]["file_writes"]["status"] == "confirmed"
    assert not state.checklist(changed)[0]["complete"]
    assert changed["scopes"]["create"] == {"cases": ["refund"], "confirmed": False, "needs_review": True}
    assert not changed["journey_status"]


def test_agent_change_cannot_reuse_other_agents_ethos(state, tmp_path):
    setup_ready(state, tmp_path)
    ethos_ready(state, tmp_path)
    changed = state.initialize(tmp_path, agent="other-agent")
    assert changed["ethos"]["confirmed_digest"] is None
    assert changed["prerequisites"]["runtime"]["status"] == "confirmed"
    assert changed["prerequisites"]["services"] == {
        "status": "unknown",
        "source": "user reply 2",
    }


@pytest.mark.parametrize("new_agent", ["account-support", "other-agent"])
@pytest.mark.parametrize("stage", ["complete", "write"])
def test_agent_change_preserves_historical_context_without_reusing_approval(state, tmp_path, new_agent, stage):
    state.initialize(tmp_path, agent="Account Support", journey="create", setup="gym")
    state.update(
        tmp_path,
        {
            "prerequisites": {
                key: {"status": "confirmed", "source": "Original agent setup reply"}
                for key in state.PREREQUISITES | state.DEFERRED
            }
        },
    )
    legacy = tmp_path / ".eval-author/first-eval.md"
    legacy.write_text("Prior scope needs review\n")
    imported = state.import_legacy(tmp_path)
    draft = tmp_path / ".eval-author/.working/draft.md"
    draft.write_text("# Intended refund behavior\n")
    findings = [{"path": "agent.py:12", "summary": "Original agent reads refund requests"}]
    answers = [{"topic": "Purpose", "question": "What outcome?", "answer": "Resolve refunds", "source": "reply 3"}]
    state.update(
        tmp_path,
        {
            "ethos": {
                "stage": "write",
                "draft": {"path": draft.relative_to(tmp_path).as_posix(), "digest": state.digest(draft.read_bytes())},
                "draft_confirmed": True,
                "draft_source": "Original agent draft approval in reply 4",
                "source_findings": findings,
                "prior_answers": answers,
                "remaining_questions": ["Which refund regions apply?"],
            }
        },
    )
    if stage == "complete":
        ethos_ready(state, tmp_path)
    original = state.update(
        tmp_path,
        {
            "scopes": {
                "create": {"cases": ["refund"], "priorities": ["accuracy"], "confirmed": True},
                "audit": {"traces": ["selected.json"], "confirmed": True},
            },
            "journey_status": {"create": "complete", "audit": "in_progress"},
            "blockers": imported["blockers"]
            + [
                {"id": "code_access", "phase": "setup", "message": "An intent source is inaccessible"},
                {"id": "review-intent", "phase": "ethos", "message": "Review a remaining boundary"},
                {"id": "select-traces", "phase": "audit", "message": "A selection remains unresolved"},
            ],
            "pending_question": "Which selected trace should be used?",
            "next_handoff": "eval-author-audit",
        },
    )
    before = state.state_path(tmp_path).read_bytes()
    changed = state.initialize(tmp_path, agent=new_agent)
    assert changed["agent"] == new_agent
    assert changed["ethos"]["stage"] == "review"
    for field in ("confirmed_digest", "confirmation_source", "validation", "draft_source"):
        assert changed["ethos"][field] is None
    assert not changed["ethos"]["draft_confirmed"]
    assert changed["ethos"]["draft"] == original["ethos"]["draft"]
    assert draft.read_text() == "# Intended refund behavior\n"
    assert changed["ethos"]["source_findings"][:-1] == findings
    assert changed["ethos"]["prior_answers"] == answers
    assert changed["ethos"]["remaining_questions"] == original["ethos"]["remaining_questions"]
    for journey, scope in original["scopes"].items():
        assert changed["scopes"][journey] == {**scope, "confirmed": False, "needs_review": True}
    assert not changed["journey_status"]
    assert changed["blockers"] == original["blockers"]
    assert changed["legacy_imports"] == original["legacy_imports"]
    for key, answer in original["prerequisites"].items():
        assert changed["prerequisites"][key] == {
            **answer,
            "status": "unknown" if key in state.AGENT_DEPENDENT else answer["status"],
        }
    history = changed["ethos"]["source_findings"][-1]
    assert Path(history["path"]).is_relative_to(".eval-author/.working")
    archive = tmp_path / history["path"]
    assert archive.read_bytes() == before
    assert archive.stem == state.digest(before)
    assert json.loads(archive.read_text())["agent"] == "Account Support"
    assert "Account Support" in history["summary"]
    assert new_agent in changed["pending_question"] and "Account Support" in changed["pending_question"]
    assert changed["next_handoff"] == "eval-author"
    assert not state.checklist(changed)[1]["complete"]
    assert state.load_state(tmp_path) == changed
    resumed = state.initialize(tmp_path, agent=new_agent)
    assert resumed["ethos"] == changed["ethos"]
    assert resumed["scopes"] == changed["scopes"]
    assert resumed["pending_question"] == changed["pending_question"]
    assert list(archive.parent.iterdir()) == [archive]
    persisted = state.state_path(tmp_path).read_bytes()
    assert state.import_legacy(tmp_path) == resumed
    assert state.state_path(tmp_path).read_bytes() == persisted
    with pytest.raises(ValueError, match="write stage requires confirmed draft"):
        state.update(tmp_path, {"ethos": {"stage": "write"}})
    assert state.state_path(tmp_path).read_bytes() == persisted


@pytest.mark.parametrize("old_agent", [None, "previous-agent"])
@pytest.mark.parametrize("resolved", [False, True])
def test_agent_assignment_preserves_only_unresolved_legacy_review(state, tmp_path, old_agent, resolved):
    state.initialize(tmp_path, agent=old_agent, journey="audit")
    legacy = tmp_path / ".eval-author/audit-progress.md"
    legacy.write_text("[x] Historical review\n")
    original = state.import_legacy(tmp_path)
    if resolved:
        original = state.update(tmp_path, {"blockers": [], "pending_question": None})
    changed = state.initialize(tmp_path, agent="new-agent")
    assert changed["legacy_imports"] == original["legacy_imports"]
    assert changed["blockers"] == original["blockers"]
    assert any(item["id"] == "legacy-review" for item in changed["blockers"]) is not resolved
    before = state.state_path(tmp_path).read_bytes()
    assert state.import_legacy(tmp_path) == changed
    assert state.state_path(tmp_path).read_bytes() == before


def test_combined_identity_setup_and_flow_change_keeps_context_review_handoff(state, tmp_path):
    setup_ready(state, tmp_path)
    original = ethos_ready(state, tmp_path)
    before = state.state_path(tmp_path).read_bytes()
    changed = state.initialize(tmp_path, agent="new-agent", journey="audit", setup="harbor")
    assert changed["agent"] == "new-agent" and changed["journey"] == "audit" and changed["setup"] == "harbor"
    assert changed["ethos"]["stage"] == "review"
    assert changed["ethos"]["confirmed_digest"] is None
    assert "assistant" in changed["pending_question"] and "new-agent" in changed["pending_question"]
    assert changed["next_handoff"] == "eval-author"
    archive = tmp_path / changed["ethos"]["source_findings"][-1]["path"]
    assert archive.read_bytes() == before
    assert json.loads(archive.read_text()) == original


@pytest.mark.parametrize("failure_stage", ["archive", "state"])
def test_agent_context_persistence_failure_leaves_current_state_intact(state, tmp_path, monkeypatch, failure_stage):
    setup_ready(state, tmp_path)
    original = ethos_ready(state, tmp_path)
    before = state.state_path(tmp_path).read_bytes()

    def failed_write(*args, **kwargs):
        raise OSError("Storage unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(state, "atomic_write" if failure_stage == "archive" else "atomic_write_json", failed_write)
        with pytest.raises(OSError, match="Storage unavailable"):
            state.initialize(tmp_path, agent="other-agent")
    assert state.state_path(tmp_path).read_bytes() == before
    assert state.load_state(tmp_path) == original
    retried = state.initialize(tmp_path, agent="other-agent")
    archive = tmp_path / retried["ethos"]["source_findings"][-1]["path"]
    assert archive.read_bytes() == before
    assert list(archive.parent.iterdir()) == [archive]
    assert retried["agent"] == "other-agent" and retried["ethos"]["confirmed_digest"] is None


@pytest.mark.parametrize("change", ["edit", "delete", "symlink"])
@pytest.mark.parametrize("review_pending", [False, True])
def test_changed_ethos_loses_approval_without_mutating_on_show(state, tmp_path, change, review_pending):
    setup_ready(state, tmp_path)
    ethos_ready(state, tmp_path)
    state.update(
        tmp_path,
        {
            "scopes": {"audit": {"traces": ["selected.json"], "confirmed": True}},
            "journey_status": {"audit": "complete"},
        },
    )
    if review_pending:
        state.update(tmp_path, {"ethos": {"stage": "review"}})
    before = state.state_path(tmp_path).read_bytes()
    ethos = tmp_path / "ETHOS.md"
    if change == "edit":
        ethos.write_text("Changed intent")
    else:
        ethos.unlink()
        if change == "symlink":
            target = tmp_path / "other.md"
            target.write_text("Other")
            ethos.symlink_to(target)
    result = state.load_state(tmp_path)
    assert result["ethos"]["confirmed_digest"] is None
    assert not state.checklist(result)[1]["complete"]
    assert result["scopes"]["audit"]["traces"] == ["selected.json"]
    assert result["scopes"]["audit"]["needs_review"]
    assert not result["journey_status"]
    assert state.state_path(tmp_path).read_bytes() == before


def test_explicit_review_must_match_saved_bytes(state, tmp_path):
    setup_ready(state, tmp_path)
    (tmp_path / "ETHOS.md").write_text("Original")
    reviewed = state.ethos_digest(tmp_path)
    (tmp_path / "ETHOS.md").write_text("Changed after review")
    with pytest.raises(ValueError, match="differs"):
        state.confirm_ethos(tmp_path, reviewed_digest=reviewed, source="reply", validation="passed")
    assert state.load_state(tmp_path)["ethos"]["confirmed_digest"] is None
    with pytest.raises(ValueError, match="confirm-ethos"):
        state.update(tmp_path, {"ethos": {"stage": "complete", "confirmed_digest": reviewed}})


def test_invalid_and_foreign_state_never_establish_approval(state, tmp_path):
    setup_ready(state, tmp_path)
    good = ethos_ready(state, tmp_path)
    path = state.state_path(tmp_path)
    for mutate in (
        lambda x: x.update(schema_version=2),
        lambda x: x.update(repository="/other/repository"),
        lambda x: x["ethos"].update(confirmation_source=None),
        lambda x: x["ethos"].update(confirmed_digest=None),
        lambda x: x["ethos"].update(stage="review", validation=None),
        lambda x: x["prerequisites"].update(runtime={"status": "confirmed"}),
    ):
        bad = copy.deepcopy(good)
        mutate(bad)
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            state.load_state(tmp_path)
    path.write_text('{"schema_version": 1, "schema_version": 2}')
    with pytest.raises(ValueError, match="Duplicate"):
        state.load_state(tmp_path)


@pytest.mark.parametrize("relocation", ["move", "copy"])
def test_repository_rebind_preserves_context_and_reopens_readiness(state, tmp_path, relocation):
    original_repo = tmp_path / "original"
    original_repo.mkdir()
    task, evidence = task_fixture(state, original_repo)
    setup_ready(state, original_repo)
    ethos_ready(state, original_repo)
    state.record_task(original_repo, task=task, status="accepted", source="Original acceptance", evidence=[evidence])
    draft = original_repo / ".eval-author/.working/draft.md"
    draft.write_text("# Original intent\n")
    findings = [{"path": "agent.py", "summary": "Original agent source"}]
    answers = [{"topic": "purpose", "question": "Which goal?", "answer": "Refunds", "source": "reply 2"}]
    original = state.update(
        original_repo,
        {
            "ethos": {
                "draft": {
                    "path": draft.relative_to(original_repo).as_posix(),
                    "digest": state.digest(draft.read_bytes()),
                },
                "source_findings": findings,
                "prior_answers": answers,
                "remaining_questions": ["Which refunds?"],
            },
            "scopes": {"create": {"cases": ["refund"], "confirmed": True, "priorities": ["accuracy"]}},
            "journey_status": {"create": "complete"},
            "blockers": [{"id": "review-case", "phase": "create", "message": "Review an unresolved case"}],
            "pending_question": "Which case should continue?",
            "next_handoff": "eval-author-first-eval",
        },
    )
    before = state.state_path(original_repo).read_bytes()
    relocated = tmp_path / "relocated"
    if relocation == "move":
        original_repo.rename(relocated)
    else:
        shutil.copytree(original_repo, relocated)
    for command in (state.load_state, state.initialize):
        with pytest.raises(ValueError, match=r"rebind.*--from-repository.*--expected-revision"):
            command(relocated)
        assert state.state_path(relocated).read_bytes() == before

    rebound = state.rebind(relocated, previous_repository=str(original_repo), expected_revision=original["revision"])
    assert rebound["repository"] == str(relocated)
    assert rebound["revision"] == original["revision"] + 1
    for field in ("agent", "journey", "setup", "blockers", "legacy_imports"):
        assert rebound[field] == original[field]
    for key, answer in original["prerequisites"].items():
        assert rebound["prerequisites"][key] == {**answer, "status": "unknown"}
    assert rebound["ethos"]["stage"] == "review"
    assert rebound["ethos"]["draft"] == original["ethos"]["draft"]
    assert rebound["ethos"]["prior_answers"] == answers
    assert rebound["ethos"]["remaining_questions"] == ["Which refunds?"]
    for field in ("confirmed_digest", "confirmation_source", "validation", "draft_source"):
        assert rebound["ethos"][field] is None
    assert not rebound["ethos"]["draft_confirmed"]
    assert rebound["ethos"]["source_findings"][:-1] == findings
    history = rebound["ethos"]["source_findings"][-1]
    archive = relocated / history["path"]
    assert archive.is_relative_to(relocated / ".eval-author/.working/history/repositories")
    assert archive.stem == state.digest(before)
    assert archive.read_bytes() == before
    assert str(original_repo) in history["summary"]
    assert rebound["scopes"]["create"] == {**original["scopes"]["create"], "confirmed": False, "needs_review": True}
    assert not rebound["journey_status"]
    assert rebound["tasks"][task]["evidence"] == original["tasks"][task]["evidence"]
    assert rebound["tasks"][task]["task_digest"] == original["tasks"][task]["task_digest"]
    assert state.task_lifecycle(relocated, rebound, task)["status"] == "draft"
    assert history["path"] in rebound["tasks"][task]["source"]
    assert str(original_repo) in rebound["pending_question"]
    assert str(relocated) in rebound["pending_question"]
    assert rebound["next_handoff"] == "eval-author"
    assert not any(row["complete"] for row in state.checklist(rebound))
    assert state.load_state(relocated) == rebound
    resumed = state.initialize(relocated)
    assert resumed["ethos"] == rebound["ethos"]
    assert resumed["tasks"] == rebound["tasks"]
    assert list(archive.parent.iterdir()) == [archive]
    if relocation == "copy":
        assert state.state_path(original_repo).read_bytes() == before
        assert state.load_state(original_repo) == original


@pytest.mark.parametrize("invalid", ["source", "revision", "already-bound", "schema"])
def test_repository_rebind_rejects_unreviewed_or_invalid_state(state, tmp_path, invalid):
    original = state.initialize(tmp_path, agent="assistant", journey="audit")
    previous_repository = "/prior/repository"
    original["repository"] = previous_repository if invalid != "already-bound" else str(tmp_path)
    if invalid == "schema":
        original["schema_version"] = 2
    state.state_path(tmp_path).write_text(json.dumps(original))
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        state.rebind(
            tmp_path,
            previous_repository="/wrong/repository" if invalid == "source" else previous_repository,
            expected_revision=original["revision"] - (invalid == "revision"),
        )
    assert state.state_path(tmp_path).read_bytes() == before
    assert not (tmp_path / ".eval-author/.working/history").exists()


@pytest.mark.parametrize("failure_stage", ["archive", "state"])
def test_repository_rebind_failure_preserves_prior_state(state, tmp_path, monkeypatch, failure_stage):
    original = state.initialize(tmp_path, agent="assistant", journey="audit")
    original["repository"] = "/prior/repository"
    state.state_path(tmp_path).write_text(json.dumps(original))
    before = state.state_path(tmp_path).read_bytes()

    def failed_write(*args, **kwargs):
        raise OSError("Storage unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(state, "atomic_write" if failure_stage == "archive" else "atomic_write_json", failed_write)
        with pytest.raises(OSError, match="Storage unavailable"):
            state.rebind(tmp_path, previous_repository=original["repository"], expected_revision=original["revision"])
    assert state.state_path(tmp_path).read_bytes() == before
    rebound = state.rebind(tmp_path, previous_repository=original["repository"], expected_revision=original["revision"])
    archive = tmp_path / rebound["ethos"]["source_findings"][-1]["path"]
    assert archive.read_bytes() == before
    assert list(archive.parent.iterdir()) == [archive]


def test_rebind_and_agent_changes_preserve_each_others_history(state, tmp_path):
    state.initialize(tmp_path, agent="first-agent", journey="audit")
    original = state.initialize(tmp_path, agent="second-agent")
    previous_history = original["ethos"]["source_findings"][0]
    original["repository"] = "/prior/repository"
    state.state_path(tmp_path).write_text(json.dumps(original))
    rebound = state.rebind(tmp_path, previous_repository=original["repository"], expected_revision=original["revision"])
    assert rebound["ethos"]["source_findings"][0] == previous_history
    changed = state.initialize(tmp_path, agent="third-agent")
    assert changed["ethos"]["source_findings"][:-1] == rebound["ethos"]["source_findings"]
    archive = tmp_path / changed["ethos"]["source_findings"][-1]["path"]
    assert json.loads(archive.read_text()) == rebound
    for finding in changed["ethos"]["source_findings"]:
        assert (tmp_path / finding["path"]).is_file()


def test_repository_rebind_cli_requires_reviewed_source_and_revision(state, tmp_path):
    original = state.initialize(tmp_path, agent="assistant", journey="audit")
    original["repository"] = "/prior/repository"
    state.state_path(tmp_path).write_text(json.dumps(original))
    command = [sys.executable, str(SCRIPTS / "journey_state.py"), "--repo", str(tmp_path)]
    rejected = subprocess.run([*command, "show"], capture_output=True, text=True)
    assert rejected.returncode == 2
    assert "rebind" in json.loads(rejected.stderr)["error"]
    assert "/prior/repository" in rejected.stderr
    result = subprocess.run(
        [
            *command,
            "rebind",
            "--from-repository",
            original["repository"],
            "--expected-revision",
            str(original["revision"]),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["state"] == state.load_state(tmp_path)


def test_interrupted_atomic_write_preserves_last_state_and_cleans_staging(state, tmp_path, monkeypatch):
    setup_ready(state, tmp_path)
    paths = importlib.import_module("working_paths")
    before = state.state_path(tmp_path).read_bytes()

    def failed_replace(*_):
        raise OSError("disk unavailable")

    monkeypatch.setattr(paths.os, "replace", failed_replace)
    with pytest.raises(OSError, match="disk unavailable"):
        state.update(tmp_path, {"pending_question": "New question"})
    assert state.state_path(tmp_path).read_bytes() == before
    assert not list(state.state_path(tmp_path).parent.glob(".journey-state.json-*"))


@pytest.mark.parametrize("operation", ["save", "update"])
def test_formatted_state_limit_preserves_previous_bytes_and_revision(state, tmp_path, monkeypatch, operation):
    monkeypatch.setattr(state, "now", lambda: "2026-10-22T12:34:56.123456+00:00")
    original = state.initialize(tmp_path, agent="assistant", journey="audit")
    before = state.state_path(tmp_path).read_bytes()
    proposed = copy.deepcopy(original)
    scope = {"create": {"cases": ["refund"] * 128}}
    proposed["scopes"] = scope
    persisted = {**proposed, "revision": proposed["revision"] + 1}
    compact_size = len(json.dumps(persisted, allow_nan=False).encode())
    formatted_size = len((json.dumps(persisted, indent=2, ensure_ascii=False) + "\n").encode())
    limit = (compact_size + formatted_size) // 2
    assert len(before) <= limit
    assert compact_size < limit < formatted_size
    monkeypatch.setattr(state, "MAX_BYTES", limit)
    with pytest.raises(ValueError, match="Journey state is too large"):
        if operation == "save":
            state.save(tmp_path, proposed)
        else:
            state.update(tmp_path, {"scopes": scope})
    assert state.state_path(tmp_path).read_bytes() == before
    assert proposed == {**original, "scopes": scope}
    assert state.load_state(tmp_path) == original
    assert not list(state.state_path(tmp_path).parent.glob(".journey-state.json-*"))


@pytest.mark.parametrize(
    "text",
    ["ASCII text " * 30, "Résumé ☃ 🦊 " * 30, 'Quotes " and escapes\n\\ ' * 30],
    ids=["ascii", "unicode", "escapes"],
)
@pytest.mark.parametrize("one_byte_over", [False, True])
def test_state_size_uses_exact_utf8_format_and_trailing_newline(state, tmp_path, monkeypatch, text, one_byte_over):
    monkeypatch.setattr(state, "now", lambda: "2026-10-22T12:34:56.123456+00:00")
    original = state.initialize(tmp_path, agent="assistant", journey="audit")
    before = state.state_path(tmp_path).read_bytes()
    proposed = {**original, "pending_question": text}
    persisted = {**proposed, "revision": proposed["revision"] + 1}
    payload = (json.dumps(persisted, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    monkeypatch.setattr(state, "MAX_BYTES", len(payload) - one_byte_over)
    if one_byte_over:
        with pytest.raises(ValueError, match="Journey state is too large"):
            state.save(tmp_path, proposed)
        assert state.state_path(tmp_path).read_bytes() == before
        assert proposed == {**original, "pending_question": text}
        assert state.load_state(tmp_path) == original
    else:
        state.save(tmp_path, proposed)
        assert state.state_path(tmp_path).read_bytes() == payload
        assert len(payload) == state.MAX_BYTES
        assert proposed == persisted
        assert state.load_state(tmp_path) == persisted


def test_failed_save_does_not_advance_in_memory_revision(state, tmp_path, monkeypatch):
    original = state.initialize(tmp_path, agent="assistant", journey="audit")
    proposed = copy.deepcopy(original)
    before = state.state_path(tmp_path).read_bytes()

    def failed_write(*args, **kwargs):
        raise OSError("Storage unavailable")

    monkeypatch.setattr(state, "atomic_write_json", failed_write)
    with pytest.raises(OSError, match="Storage unavailable"):
        state.save(tmp_path, proposed)
    assert proposed == original
    assert state.state_path(tmp_path).read_bytes() == before
    assert state.load_state(tmp_path) == original


def test_revision_digit_growth_cannot_exceed_persisted_byte_limit(state, tmp_path, monkeypatch):
    monkeypatch.setattr(state, "now", lambda: "2026-10-22T12:34:56.123456+00:00")
    original = state.initialize(tmp_path, agent="assistant", journey="audit")
    original["revision"] = 9
    state.atomic_write_json(state.state_path(tmp_path), original)
    before = state.state_path(tmp_path).read_bytes()
    proposed = copy.deepcopy(original)
    monkeypatch.setattr(state, "MAX_BYTES", len(before))
    with pytest.raises(ValueError, match="Journey state is too large"):
        state.save(tmp_path, proposed)
    assert state.state_path(tmp_path).read_bytes() == before
    assert proposed == original
    assert state.load_state(tmp_path) == original


def test_optimistic_revision_rejects_stale_answer(state, tmp_path):
    first = state.initialize(tmp_path, journey="create")
    state.update(tmp_path, {"pending_question": "Current"}, expected_revision=first["revision"])
    with pytest.raises(ValueError, match="State changed"):
        state.update(tmp_path, {"pending_question": "Old"}, expected_revision=first["revision"])
    assert state.load_state(tmp_path)["pending_question"] == "Current"


def test_legacy_import_is_idempotent_keeps_originals_and_never_imports_approval(state, tmp_path):
    state.initialize(tmp_path, journey="audit")
    old = tmp_path / ".eval-author/audit-progress.md"
    old.write_text("[x] Ethos approved\nNext: measure selected.json\n")
    imported = state.import_legacy(tmp_path)
    assert imported["ethos"]["confirmed_digest"] is None
    assert len(imported["legacy_imports"]) == 1
    snapshot = imported["legacy_imports"][0]
    assert (tmp_path / snapshot["archive"]).read_bytes() == old.read_bytes()
    before = state.state_path(tmp_path).read_bytes()
    assert state.import_legacy(tmp_path) == imported
    assert state.state_path(tmp_path).read_bytes() == before
    old.write_text("A conflicting later note")
    changed = state.import_legacy(tmp_path)
    assert changed["legacy_imports"][-1]["status"] == "changed"
    assert len(changed["legacy_imports"]) == 2
    assert changed["blockers"][0]["id"] == "legacy-review"
    assert (tmp_path / snapshot["archive"]).read_text().startswith("[x]")


def test_working_storage_rejects_escape_and_symlink(state, tmp_path):
    paths = importlib.import_module("working_paths")
    with pytest.raises(ValueError, match="beneath"):
        paths.working_path(tmp_path, "..", "outside")
    target = tmp_path / "outside"
    target.mkdir()
    (tmp_path / ".eval-author").mkdir()
    (tmp_path / ".eval-author/.working").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        state.initialize(tmp_path, journey="create")
    assert list(target.iterdir()) == []


def test_owned_scratch_stays_under_working_and_cleans_only_itself(state, tmp_path):
    paths = importlib.import_module("working_paths")
    sentinel = paths.working_path(tmp_path, "notes.txt")
    sentinel.write_text("Keep")
    with paths.scratch_directory(tmp_path, prefix="trial-") as scratch:
        assert scratch.is_relative_to(tmp_path / ".eval-author/.working/tmp")
        (scratch / "transient").write_text("Temporary")
    assert not scratch.exists()
    assert sentinel.read_text() == "Keep"


def test_read_only_cli_never_initializes_missing_state(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "journey_state.py"), "--repo", str(tmp_path), "show"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert json.loads(result.stderr)["persisted"] is False
    assert list(tmp_path.iterdir()) == []


def test_lock_conflict_preserves_existing_state(state, tmp_path):
    paths = importlib.import_module("working_paths")
    original = state.initialize(tmp_path, journey="create")
    with paths.working_lock(paths.working_path(tmp_path, "journey-state.lock")):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "journey_state.py"), "--repo", str(tmp_path), "init", "--journey", "audit"],
            capture_output=True,
            text=True,
        )
    assert result.returncode == 2
    assert state.load_state(tmp_path) == original


def test_unwritable_storage_does_not_claim_persistence(state, tmp_path, monkeypatch):
    original = state.initialize(tmp_path, journey="create")

    def deny_write(*_):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(state, "atomic_write_json", deny_write)
    with pytest.raises(PermissionError, match="read-only"):
        state.update(tmp_path, {"pending_question": "Not saved"})
    assert state.load_state(tmp_path) == original


@pytest.mark.parametrize("provider,name", [("gym", "NeMo Gym"), ("harbor", "Harbor")])
@pytest.mark.parametrize("journey", ["create", "expand"])
@pytest.mark.parametrize("status", ["missing", "unknown"])
@pytest.mark.parametrize("ethos_confirmed", [False, True])
def test_required_runtime_blocks_onboarding_until_resolved(
    state, tmp_path, provider, name, journey, status, ethos_confirmed
):
    setup_ready(state, tmp_path, journey=journey, setup=provider)
    prior_ethos = ethos_ready(state, tmp_path)["ethos"] if ethos_confirmed else state.load_state(tmp_path)["ethos"]
    finding = (
        f"{name} CLI was not found" if status == "missing" else f"Access to the {name} runtime could not be checked"
    )
    question = f"Make the required {name} runtime available" if status == "missing" else f"Resolve access to {name}"
    saved = state.update(
        tmp_path,
        {
            "prerequisites": {
                "runtime": {"status": status, "source": finding},
                "model_credentials": {"status": "missing", "source": "No credentials configured for later agent runs"},
            },
            "blockers": [{"id": "runtime", "phase": "setup", "message": finding}],
            "pending_question": question,
            "next_handoff": "eval-author",
        },
    )
    assert state.load_state(tmp_path) == saved
    resumed = state.initialize(tmp_path, agent="assistant", journey=journey, setup=provider)
    for blocked in (saved, resumed):
        assert blocked["prerequisites"]["runtime"] == {"status": status, "source": finding}
        assert blocked["blockers"] == saved["blockers"]
        assert blocked["pending_question"] == question
        assert blocked["next_handoff"] == "eval-author"
        assert blocked["ethos"] == prior_ethos
        rows = state.checklist(blocked)
        assert rows[0]["blocked"] and not rows[0]["complete"]
        assert rows[1]["complete"] is ethos_confirmed
        assert not rows[2]["complete"]
        assert not any(row["current"] for row in rows)

    evidence = f"Current {name} authoring runtime check passed"
    resolved = state.update(
        tmp_path,
        {
            "prerequisites": {"runtime": {"status": "verified", "source": evidence}},
            "blockers": [],
            "pending_question": None,
            "next_handoff": "eval-author-first-eval" if ethos_confirmed else "ethos-orchestrate",
        },
    )
    assert state.load_state(tmp_path) == resolved
    assert resolved["prerequisites"]["runtime"] == {"status": "verified", "source": evidence}
    assert resolved["prerequisites"]["model_credentials"]["status"] == "missing"
    assert resolved["ethos"] == prior_ethos
    assert state.checklist(resolved)[0]["complete"]
    assert state.checklist(resolved)[2 if ethos_confirmed else 1]["current"]
    confirmed = resolved if ethos_confirmed else ethos_ready(state, tmp_path)
    assert state.checklist(confirmed)[2]["current"]
    assert not state.checklist(confirmed)[2]["complete"]


def test_cli_roundtrip_matches_helpers_and_updates_no_domain_artifacts(state, tmp_path):
    (tmp_path / "README.md").write_text("User source")
    init = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "journey_state.py"),
            "--repo",
            str(tmp_path),
            "init",
            "--agent",
            "assistant",
            "--journey",
            "expand",
            "--setup",
            "harbor",
        ],
        text=True,
        capture_output=True,
    )
    assert init.returncode == 0, init.stderr
    initial = json.loads(init.stdout)
    payload = tmp_path / ".eval-author/.working/answer.json"
    payload.write_text(
        json.dumps(
            {"scopes": {"expand": {"cases": ["edge-case"], "confirmed": True}}, "pending_question": "Approve grading?"}
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "journey_state.py"),
            "--repo",
            str(tmp_path),
            "update",
            "--input",
            str(payload),
            "--expected-revision",
            str(initial["state"]["revision"]),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["state"] == state.load_state(tmp_path)
    assert (tmp_path / "README.md").read_text() == "User source"
    assert {p.name for p in tmp_path.iterdir()} == {"README.md", ".eval-author"}
    assert not (tmp_path / "ETHOS.md").exists()


def task_fixture(state, repo):
    state.initialize(repo, agent="assistant", journey="create", setup="gym")
    task = repo / ".eval-author/tasks/refund"
    task.mkdir(parents=True)
    (task / "README.md").write_text("# Refund\n")
    (task / "grader.py").write_text("assert result == expected\n")
    evidence = repo / ".eval-author/.working/control-result.json"
    evidence.write_text('{"result": "passed"}\n')
    return task.relative_to(repo).as_posix(), evidence.relative_to(repo).as_posix()


def test_legacy_state_and_successful_run_do_not_infer_task_acceptance(state, tmp_path):
    task, _ = task_fixture(state, tmp_path)
    legacy = state.load_state(tmp_path)
    legacy.pop("tasks")
    state.state_path(tmp_path).write_text(json.dumps(legacy))
    before = state.state_path(tmp_path).read_bytes()
    loaded = state.load_state(tmp_path)
    assert loaded["schema_version"] == 1 and loaded["tasks"] == {}
    assert state.task_lifecycle(tmp_path, loaded, task) == {
        "status": "unknown",
        "reason": "No task acceptance decision is recorded",
    }
    assert state.state_path(tmp_path).read_bytes() == before


def test_task_decision_is_explicit_and_read_only_resolution_preserves_provenance(state, tmp_path):
    task, evidence = task_fixture(state, tmp_path)
    drafted = state.record_task(tmp_path, task=task, status="draft", source="Case authoring started")
    assert state.task_lifecycle(tmp_path, drafted, task)["status"] == "draft"
    accepted = state.record_task(
        tmp_path, task=task, status="accepted", source="First-eval review, reply 9", evidence=[evidence]
    )
    before = state.state_path(tmp_path).read_bytes()
    assert state.task_lifecycle(tmp_path, accepted, task) == {
        "status": "accepted",
        "reason": "First-eval review, reply 9",
    }
    assert accepted["tasks"][task]["evidence"] == [
        {"path": evidence, "digest": state.digest((tmp_path / evidence).read_bytes())}
    ]
    assert state.state_path(tmp_path).read_bytes() == before


def test_native_manifest_uses_nearest_registered_task_root(state, tmp_path):
    task, evidence = task_fixture(state, tmp_path)
    manifest = tmp_path / task / "environments/refund/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("name: refund\n")
    accepted = state.record_task(tmp_path, task=task, status="accepted", source="Reviewed", evidence=[evidence])
    assert state.task_lifecycle(tmp_path, accepted, manifest.relative_to(tmp_path).as_posix())["status"] == "accepted"
    inner = manifest.parent.relative_to(tmp_path).as_posix()
    state.record_task(tmp_path, task=inner, status="draft", source="Nested task under revision")
    latest = state.load_state(tmp_path)
    assert state.task_lifecycle(tmp_path, latest, manifest.relative_to(tmp_path).as_posix())["status"] == "draft"
    assert state.task_lifecycle(tmp_path, latest, task)["status"] == "accepted"
    assert state.task_lifecycle(tmp_path, latest, task + "-other")["status"] == "unknown"


@pytest.mark.parametrize("change", ["edit", "add", "remove", "mode", "readme", "evidence", "missing_evidence"])
def test_changed_content_or_evidence_reopens_task_readiness(state, tmp_path, change):
    task, evidence = task_fixture(state, tmp_path)
    accepted = state.record_task(tmp_path, task=task, status="accepted", source="Reviewed", evidence=[evidence])
    original_record = copy.deepcopy(accepted["tasks"][task])
    target = tmp_path / task / "grader.py"
    if change == "edit":
        target.write_text("assert True\n")
    elif change == "add":
        (target.parent / "fixture.json").write_text("{}")
    elif change == "remove":
        target.unlink()
    elif change == "mode":
        target.chmod(0o700)
    elif change == "readme":
        (target.parent / "README.md").write_text("Changed task instructions")
    elif change == "evidence":
        (tmp_path / evidence).write_text('{"result": "failed"}')
    else:
        (tmp_path / evidence).unlink()
    before = state.state_path(tmp_path).read_bytes()
    result = state.task_lifecycle(tmp_path, state.load_state(tmp_path), task)
    assert result["status"] == "draft"
    assert "changed" in result["reason"] or "unavailable" in result["reason"]
    assert state.load_state(tmp_path)["tasks"][task] == original_record
    assert state.state_path(tmp_path).read_bytes() == before


def test_ignored_runtime_caches_do_not_invalidate_task_acceptance(state, tmp_path):
    task, evidence = task_fixture(state, tmp_path)
    accepted = state.record_task(tmp_path, task=task, status="accepted", source="Reviewed", evidence=[evidence])
    cache = tmp_path / task / "__pycache__"
    cache.mkdir()
    (cache / "grader.pyc").write_bytes(b"compiled")
    assert state.task_lifecycle(tmp_path, accepted, task)["status"] == "accepted"


@pytest.mark.parametrize("task_path", [".", "", "../task", "/tmp/task", "tasks/../task", "./tasks/task"])
def test_task_paths_are_bounded_to_repository_and_canonical(state, tmp_path, task_path):
    task_fixture(state, tmp_path)
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        state.record_task(tmp_path, task=task_path, status="draft", source="Started")
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize(
    "bad", ["empty_source", "long_source", "no_evidence", "duplicate", "unknown", "traversal", "self_state"]
)
def test_invalid_task_decision_preserves_saved_state(state, tmp_path, bad):
    task, evidence = task_fixture(state, tmp_path)
    kwargs = {"task": task, "status": "accepted", "source": "Reviewed", "evidence": [evidence]}
    if bad == "empty_source":
        kwargs["source"] = ""
    elif bad == "long_source":
        kwargs["source"] = "x" * 2001
    elif bad == "no_evidence":
        kwargs["evidence"] = []
    elif bad == "duplicate":
        kwargs["evidence"] = [evidence, evidence]
    elif bad == "unknown":
        kwargs["status"] = "unknown"
    elif bad == "traversal":
        kwargs["evidence"] = ["../outside.json"]
    else:
        kwargs["task"] = ".eval-author"
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        state.record_task(tmp_path, **kwargs)
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("target", ["task", "parent", "content", "evidence"])
def test_task_lifecycle_rejects_symlinks_without_overwriting_state(state, tmp_path, target):
    task, evidence = task_fixture(state, tmp_path)
    accepted = state.record_task(tmp_path, task=task, status="accepted", source="Reviewed", evidence=[evidence])
    before = state.state_path(tmp_path).read_bytes()
    if target in {"task", "parent"}:
        original = tmp_path / task if target == "task" else (tmp_path / task).parent
        relocated = tmp_path / "relocated"
        original.rename(relocated)
        original.symlink_to(relocated, target_is_directory=True)
    elif target == "content":
        (tmp_path / task / "linked").symlink_to(tmp_path / evidence)
    else:
        original = tmp_path / evidence
        original.unlink()
        original.symlink_to(tmp_path / task / "README.md")
    assert state.task_lifecycle(tmp_path, accepted, task)["status"] == "draft"
    with pytest.raises(ValueError):
        state.record_task(tmp_path, task=task, status="accepted", source="Reviewed", evidence=[evidence])
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("limit", ["MAX_TASK_ENTRIES", "MAX_TASK_BYTES", "MAX_BYTES"])
def test_task_content_and_evidence_reads_are_bounded(state, tmp_path, monkeypatch, limit):
    task, evidence = task_fixture(state, tmp_path)
    before = state.state_path(tmp_path).read_bytes()
    if limit == "MAX_BYTES":
        # Keep the state readable while making the selected evidence oversized.
        (tmp_path / evidence).write_bytes(b"x" * (state.MAX_BYTES + 1))
    else:
        monkeypatch.setattr(state, limit, 1)
    with pytest.raises(ValueError):
        state.record_task(tmp_path, task=task, status="accepted", source="Reviewed", evidence=[evidence])
    assert state.state_path(tmp_path).read_bytes() == before


def test_task_lifecycle_cli_records_decision_without_changing_task_or_evidence(state, tmp_path):
    task, evidence = task_fixture(state, tmp_path)
    before_task = state.task_digest(tmp_path, task)
    before_evidence = (tmp_path / evidence).read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "journey_state.py"),
            "--repo",
            str(tmp_path),
            "record-task",
            "--task",
            task,
            "--status",
            "accepted",
            "--source",
            "First-eval review",
            "--evidence",
            evidence,
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    recorded = json.loads(result.stdout)["state"]
    assert state.task_lifecycle(tmp_path, recorded, task)["status"] == "accepted"
    assert state.task_digest(tmp_path, task) == before_task
    assert (tmp_path / evidence).read_bytes() == before_evidence


def test_show_exposes_current_readiness_separately_from_prior_acceptance(state, tmp_path):
    task, evidence = task_fixture(state, tmp_path)
    state.record_task(tmp_path, task=task, status="accepted", source="Reviewed", evidence=[evidence])
    (tmp_path / task / "grader.py").write_text("changed")
    before = state.state_path(tmp_path).read_bytes()
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "journey_state.py"), "--repo", str(tmp_path), "show"],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["state"]["tasks"][task]["status"] == "accepted"
    assert output["task_readiness"][task]["status"] == "draft"
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("bad", ["unknown", "path", "digest", "fields", "evidence"])
def test_invalid_task_records_cannot_establish_readiness(state, tmp_path, bad):
    task, evidence = task_fixture(state, tmp_path)
    saved = state.record_task(tmp_path, task=task, status="accepted", source="Reviewed", evidence=[evidence])
    if bad == "unknown":
        saved["tasks"][task]["status"] = "unknown"
    elif bad == "path":
        saved["tasks"]["../escape"] = saved["tasks"].pop(task)
    elif bad == "digest":
        saved["tasks"][task]["task_digest"] = "not-a-hash"
    elif bad == "fields":
        saved["tasks"][task]["extra"] = "unexpected"
    else:
        saved["tasks"][task]["evidence"] = []
    state.state_path(tmp_path).write_text(json.dumps(saved))
    with pytest.raises(ValueError):
        state.load_state(tmp_path)


def test_interrupted_acceptance_write_keeps_prior_draft(state, tmp_path, monkeypatch):
    task, evidence = task_fixture(state, tmp_path)
    state.record_task(tmp_path, task=task, status="draft", source="Started")
    before = state.state_path(tmp_path).read_bytes()
    paths = importlib.import_module("working_paths")

    def failed_replace(*_):
        raise OSError("disk unavailable")

    monkeypatch.setattr(paths.os, "replace", failed_replace)
    with pytest.raises(OSError, match="disk unavailable"):
        state.record_task(tmp_path, task=task, status="accepted", source="Reviewed", evidence=[evidence])
    assert state.state_path(tmp_path).read_bytes() == before
    assert state.task_lifecycle(tmp_path, state.load_state(tmp_path), task)["status"] == "draft"


def test_scoped_task_creation_records_status_without_full_onboarding(state, tmp_path):
    task = tmp_path / "existing-suite/refund"
    task.mkdir(parents=True)
    (task / "instruction.md").write_text("Check refunds")
    saved = state.record_task(tmp_path, task="existing-suite/refund", status="draft", source="Scoped creation")
    assert saved["journey"] is None and saved["agent"] is None and saved["setup"] is None
    assert saved["ethos"]["stage"] == "explore"
    assert state.view(saved)["checklist"] == []
    assert state.task_lifecycle(tmp_path, saved, "existing-suite/refund")["status"] == "draft"


def test_fresh_session_recovers_interview_and_agreed_draft_across_goal_switch(state, tmp_path):
    setup_ready(state, tmp_path)
    answers = [{"topic": "Purpose", "question": "What outcome?", "answer": "Resolve refunds", "source": "reply 3"}]
    findings = [{"path": "agent.py:12", "summary": "The agent can read order status."}]
    state.update(tmp_path, {"ethos": {"stage": "interview", "prior_answers": answers, "source_findings": findings}})
    restarted = state.initialize(tmp_path, journey="expand")
    assert restarted["ethos"]["prior_answers"] == answers
    assert restarted["ethos"]["source_findings"] == findings
    draft = tmp_path / ".eval-author/.working/ethos/draft.md"
    draft.parent.mkdir()
    draft.write_text("# The proposed intended behavior\n")
    reference = {"path": draft.relative_to(tmp_path).as_posix(), "digest": state.digest(draft.read_bytes())}
    state.update(
        tmp_path,
        {"ethos": {"stage": "write", "draft": reference, "draft_confirmed": True, "draft_source": "reply 7"}},
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "journey_state.py"), "--repo", str(tmp_path), "show"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    resumed = json.loads(result.stdout)["state"]["ethos"]
    assert resumed["draft_confirmed"] and resumed["stage"] == "write"
    assert resumed["prior_answers"] == answers
    assert (tmp_path / resumed["draft"]["path"]).read_bytes() == draft.read_bytes()
    draft.write_text("Changed after agreement")
    stale = state.load_state(tmp_path)
    assert stale["ethos"]["stage"] == "draft"
    assert not stale["ethos"]["draft_confirmed"]
    assert stale["ethos"]["prior_answers"] == answers
    assert not (tmp_path / "ETHOS.md").exists()


def test_draft_approval_requires_recoverable_exact_content(state, tmp_path):
    setup_ready(state, tmp_path)
    with pytest.raises(ValueError, match="recoverable"):
        state.update(tmp_path, {"ethos": {"draft_confirmed": True, "draft_source": "reply"}})
    with pytest.raises(ValueError, match="confirmed draft"):
        state.update(tmp_path, {"ethos": {"stage": "write"}})
    for path in ("../draft.md", "/outside/draft.md", ".eval-author/.working/../../draft.md"):
        with pytest.raises(ValueError, match="under"):
            state.update(tmp_path, {"ethos": {"draft": {"path": path, "digest": "0" * 64}}})
    draft = tmp_path / ".eval-author/.working/draft.md"
    draft.write_text("Changed content")
    with pytest.raises(ValueError, match="differs"):
        state.update(
            tmp_path,
            {
                "ethos": {
                    "draft": {"path": ".eval-author/.working/draft.md", "digest": "0" * 64},
                    "draft_confirmed": True,
                    "draft_source": "reply",
                }
            },
        )
    assert not state.load_state(tmp_path)["ethos"]["draft_confirmed"]


def test_changed_saved_ethos_retains_interview_context_for_targeted_review(state, tmp_path):
    setup_ready(state, tmp_path)
    answers = [{"topic": "Boundaries", "question": "Refund cap?", "answer": "$50", "source": "reply 3"}]
    state.update(tmp_path, {"ethos": {"prior_answers": answers}})
    ethos_ready(state, tmp_path)
    (tmp_path / "ETHOS.md").write_text("Changed intent")
    resumed = state.load_state(tmp_path)
    assert resumed["ethos"]["stage"] == "review"
    assert resumed["ethos"]["prior_answers"] == answers
    assert resumed["ethos"]["confirmed_digest"] is None


def test_replacing_draft_reference_does_not_reuse_prior_content_approval(state, tmp_path):
    setup_ready(state, tmp_path)
    old = tmp_path / ".eval-author/.working/original.md"
    old.write_text("Original intended behavior")
    state.update(
        tmp_path,
        {
            "ethos": {
                "stage": "write",
                "draft_confirmed": True,
                "draft_source": "reply 5",
                "draft": {"path": old.relative_to(tmp_path).as_posix(), "digest": state.digest(old.read_bytes())},
            }
        },
    )
    new = old.with_name("revised.md")
    new.write_text("Materially different behavior")
    changed = state.update(
        tmp_path,
        {
            "ethos": {
                "stage": "draft",
                "draft": {"path": new.relative_to(tmp_path).as_posix(), "digest": state.digest(new.read_bytes())},
            }
        },
    )
    assert not changed["ethos"]["draft_confirmed"]
    assert changed["ethos"]["draft_source"] is None
    with pytest.raises(ValueError, match="confirmed draft"):
        state.update(tmp_path, {"ethos": {"stage": "write"}})
    accepted = state.update(tmp_path, {"ethos": {"stage": "write", "draft_confirmed": True, "draft_source": "reply 7"}})
    assert accepted["ethos"]["draft_confirmed"]


def prerequisite_history(state, repo, *, draft_only=False):
    """Retain prior approvals that prerequisite collection must not inspect or replace."""
    task, evidence = task_fixture(state, repo)
    setup_ready(state, repo)
    draft = repo / ".eval-author/.working/draft.md"
    draft.write_text("# Agreed intent\n")
    state.update(
        repo,
        {
            "ethos": {
                "stage": "write",
                "draft": {"path": draft.relative_to(repo).as_posix(), "digest": state.digest(draft.read_bytes())},
                "draft_confirmed": True,
                "draft_source": "Earlier draft approval",
                "prior_answers": [
                    {"topic": "purpose", "question": "What goal?", "answer": "Refunds", "source": "reply 2"}
                ],
            }
        },
    )
    if not draft_only:
        ethos_ready(state, repo)
    state.record_task(repo, task=task, status="accepted", source="Earlier acceptance", evidence=[evidence])
    saved = state.update(
        repo,
        {
            "scopes": {"create": {"cases": ["refund"], "confirmed": True}},
            "journey_status": {"create": "complete"},
            "pending_question": "Earlier pending question",
            "next_handoff": "eval-author-first-eval",
        },
    )
    return saved, [repo / "ETHOS.md", draft, repo / task, repo / evidence]


def forbid_artifact_checks(state, monkeypatch, paths):
    def fail(*args, **kwargs):
        raise AssertionError("Prerequisite state-only command checked an artifact")

    for name in ("ethos_digest", "draft_digest", "task_digest", "task_lifecycle", "_file_digest"):
        monkeypatch.setattr(state, name, fail)
    for name in ("open", "stat", "lstat"):
        original = getattr(Path, name)

        def guard(path, *args, _original=original, **kwargs):
            if any(path == target or path.is_relative_to(target) for target in paths):
                fail()
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(Path, name, guard)


@pytest.mark.parametrize("draft_only", [False, True])
def test_state_only_resume_and_pending_review_never_check_artifacts(state, tmp_path, monkeypatch, draft_only):
    original, artifacts = prerequisite_history(state, tmp_path, draft_only=draft_only)
    before = state.state_path(tmp_path).read_bytes()
    forbid_artifact_checks(state, monkeypatch, artifacts)
    assert state.load_state(tmp_path, state_only=True) == original
    assert state.state_path(tmp_path).read_bytes() == before
    resumed = state.initialize(tmp_path, agent="assistant", journey="create", setup="gym", state_only=True)
    assert resumed["revision"] == original["revision"] + 1
    for field in ("ethos", "tasks", "scopes", "journey_status", "pending_question", "next_handoff"):
        assert resumed[field] == original[field]
    pending = state.update(
        tmp_path,
        {
            "ethos": {"stage": "review"},
            "prerequisites": {"runtime": {"status": "unknown", "source": "Current probe is pending"}},
            "blockers": [{"id": "runtime", "phase": "setup", "message": "Finish the runtime check"}],
            "pending_question": "Resolve the runtime location",
            "next_handoff": "eval-author",
        },
        expected_revision=resumed["revision"],
        state_only=True,
    )
    assert pending["revision"] == resumed["revision"] + 1
    assert pending["ethos"] == {**original["ethos"], "stage": "review"}
    for field in ("tasks", "scopes", "journey_status"):
        assert pending[field] == original[field]
    output = state.view(pending, state_only=True)
    assert output["artifact_checks_deferred"] is True
    assert output["task_readiness"] is None
    assert not any(row["complete"] for row in output["checklist"])
    assert not any(row["current"] for row in output["checklist"])
    assert state.load_state(tmp_path, state_only=True) == pending


def test_state_only_fresh_init_and_identity_changes_only_touch_state(state, tmp_path, monkeypatch):
    (tmp_path / "ETHOS.md").write_text("Historical intent\n")
    forbid_artifact_checks(state, monkeypatch, [tmp_path / "ETHOS.md"])
    first = state.initialize(tmp_path, agent="first-agent", journey="create", setup="gym", state_only=True)
    assert first["revision"] == 1 and first["ethos"]["stage"] == "explore"
    before = state.state_path(tmp_path).read_bytes()
    changed = state.initialize(tmp_path, agent="second-agent", journey="audit", state_only=True)
    assert changed["agent"] == "second-agent" and changed["journey"] == "audit"
    archive = tmp_path / changed["ethos"]["source_findings"][-1]["path"]
    assert archive.read_bytes() == before
    assert changed["ethos"]["confirmed_digest"] is None


@pytest.mark.parametrize("draft_only", [False, True])
@pytest.mark.parametrize("command", ["init", "show", "update"])
def test_state_only_cli_defers_artifacts_and_current_approval(
    state, tmp_path, monkeypatch, capsys, draft_only, command
):
    original, artifacts = prerequisite_history(state, tmp_path, draft_only=draft_only)
    payload = tmp_path / ".eval-author/.working/prerequisite-update.json"
    payload.write_text(json.dumps({"ethos": {"stage": "review"}, "pending_question": "Review intent after checks"}))
    before = state.state_path(tmp_path).read_bytes()
    arguments = ["journey_state.py", "--repo", str(tmp_path), "--state-only", command]
    if command == "init":
        arguments += ["--agent", "assistant", "--journey", "create", "--setup", "gym"]
    elif command == "update":
        arguments += ["--input", str(payload), "--expected-revision", str(original["revision"])]
    monkeypatch.setattr(sys, "argv", arguments)
    forbid_artifact_checks(state, monkeypatch, artifacts)
    assert state.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["artifact_checks_deferred"] is True
    assert output["task_readiness"] is None
    assert [row["complete"] for row in output["checklist"]] == [True, False, False]
    assert output["checklist"][1]["current"]
    assert output["state"]["tasks"] == original["tasks"]
    assert output["state"]["scopes"] == original["scopes"]
    assert output["state"]["ethos"] == {
        **original["ethos"],
        "stage": "review" if command == "update" else original["ethos"]["stage"],
    }
    if command == "show":
        assert state.state_path(tmp_path).read_bytes() == before
    else:
        assert output["state"]["revision"] == original["revision"] + 1


@pytest.mark.parametrize(
    "patch",
    [
        {"ethos": {"stage": "complete"}},
        {"ethos": {"stage": "write"}},
        {"ethos": {"stage": "draft"}},
        {"ethos": {"stage": "review", "draft_confirmed": True, "draft_source": "Invented approval"}},
        {"ethos": {"draft": {"path": ".eval-author/.working/invented.md", "digest": "0" * 64}}},
        {"ethos": {"confirmed_digest": "0" * 64}},
        {"ethos": {"confirmation_source": "Invented approval"}},
        {"ethos": {"validation": "Invented validation"}},
        {"ethos": {"prior_answers": []}},
        {"ethos": None},
        {"scopes": {"create": {"confirmed": True}}},
        {"scopes": {}},
        {"journey_status": {"create": "complete"}},
        {"tasks": {}},
    ],
)
def test_state_only_rejects_approval_or_content_updates(state, tmp_path, monkeypatch, patch):
    original, artifacts = prerequisite_history(state, tmp_path)
    before = state.state_path(tmp_path).read_bytes()
    forbid_artifact_checks(state, monkeypatch, artifacts)
    with pytest.raises(ValueError, match="State-only updates|workflow fields"):
        state.update(tmp_path, patch, state_only=True)
    assert state.state_path(tmp_path).read_bytes() == before
    assert state.load_state(tmp_path, state_only=True) == original


@pytest.mark.parametrize(
    "arguments",
    [
        ["confirm-ethos", "--digest", "0" * 64, "--source", "Invented", "--validation", "Invented"],
        ["record-task", "--task", "invented", "--status", "accepted", "--source", "Invented"],
        ["import-legacy"],
        ["rebind", "--from-repository", "/previous", "--expected-revision", "1"],
    ],
)
def test_state_only_cli_rejects_unsupported_commands_without_writes(state, tmp_path, monkeypatch, capsys, arguments):
    original, artifacts = prerequisite_history(state, tmp_path)
    before = state.state_path(tmp_path).read_bytes()
    forbid_artifact_checks(state, monkeypatch, artifacts)
    monkeypatch.setattr(sys, "argv", ["journey_state.py", "--repo", str(tmp_path), "--state-only", *arguments])
    assert state.main() == 2
    output = json.loads(capsys.readouterr().err)
    assert "--state-only supports prerequisite init, show, and update only" in output["error"]
    assert output["persisted"] is False
    assert state.state_path(tmp_path).read_bytes() == before
    assert state.load_state(tmp_path, state_only=True) == original


@pytest.mark.parametrize("artifact", ["ethos", "draft", "evidence"])
@pytest.mark.parametrize("change", ["edit", "delete"])
def test_normal_refresh_after_state_only_still_invalidates_changed_artifacts(state, tmp_path, artifact, change):
    original, artifacts = prerequisite_history(state, tmp_path, draft_only=artifact == "draft")
    selected = artifacts[{"ethos": 0, "draft": 1, "evidence": 3}[artifact]]
    if change == "delete":
        selected.unlink()
    else:
        selected.write_text("Changed artifact\n")
    pending = state.update(tmp_path, {"ethos": {"stage": "review"}}, state_only=True)
    assert pending["ethos"] == {**original["ethos"], "stage": "review"}
    assert state.load_state(tmp_path, state_only=True) == pending
    before = state.state_path(tmp_path).read_bytes()
    refreshed = state.load_state(tmp_path)
    output = state.view(refreshed)
    assert output["artifact_checks_deferred"] is False
    if artifact == "ethos":
        assert refreshed["ethos"]["confirmed_digest"] is None
        assert not refreshed["ethos"]["draft_confirmed"]
        assert refreshed["scopes"]["create"] == {"cases": ["refund"], "confirmed": False, "needs_review": True}
        assert not refreshed["journey_status"]
    elif artifact == "draft":
        assert refreshed["ethos"]["stage"] == "draft"
        assert not refreshed["ethos"]["draft_confirmed"]
        assert refreshed["ethos"]["draft_source"] is None
    else:
        task = next(iter(original["tasks"]))
        assert output["task_readiness"][task]["status"] == "draft"
        assert refreshed["tasks"] == original["tasks"]
    assert refreshed["ethos"]["prior_answers"] == original["ethos"]["prior_answers"]
    assert state.state_path(tmp_path).read_bytes() == before


def test_state_only_updates_keep_revision_and_atomic_storage_guards(state, tmp_path, monkeypatch):
    original, artifacts = prerequisite_history(state, tmp_path, draft_only=True)
    before = state.state_path(tmp_path).read_bytes()
    forbid_artifact_checks(state, monkeypatch, artifacts)
    patch = {"pending_question": "Current runtime question", "ethos": {"stage": "review"}}
    with pytest.raises(ValueError, match="State changed"):
        state.update(tmp_path, patch, expected_revision=original["revision"] - 1, state_only=True)
    assert state.state_path(tmp_path).read_bytes() == before
    paths = importlib.import_module("working_paths")

    def failed_replace(*args):
        raise OSError("Storage unavailable")

    with monkeypatch.context() as failing:
        failing.setattr(paths.os, "replace", failed_replace)
        with pytest.raises(OSError, match="Storage unavailable"):
            state.update(tmp_path, patch, expected_revision=original["revision"], state_only=True)
    assert state.state_path(tmp_path).read_bytes() == before
    assert not list(state.state_path(tmp_path).parent.glob(".journey-state.json-*"))
    updated = state.update(tmp_path, patch, expected_revision=original["revision"], state_only=True)
    assert updated["revision"] == original["revision"] + 1
    assert state.load_state(tmp_path, state_only=True) == updated


@pytest.mark.parametrize("invalid", ["schema", "repository", "duplicate"])
def test_state_only_still_validates_saved_json_and_repository(state, tmp_path, invalid):
    original = state.initialize(tmp_path, agent="assistant", journey="create", setup="gym", state_only=True)
    if invalid == "schema":
        original["schema_version"] = 2
    elif invalid == "repository":
        original["repository"] = "/another/repository"
    state.state_path(tmp_path).write_text(
        '{"schema_version": 1, "schema_version": 2}' if invalid == "duplicate" else json.dumps(original)
    )
    before = state.state_path(tmp_path).read_bytes()
    for operation in (state.load_state, state.initialize):
        with pytest.raises(ValueError):
            operation(tmp_path, state_only=True)
        assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("existing_scope", [False, True])
@pytest.mark.parametrize("via_cli", [False, True])
def test_state_only_audit_framework_observation_survives_blocked_setup(
    state, tmp_path, monkeypatch, capsys, existing_scope, via_cli
):
    _, artifacts = prerequisite_history(state, tmp_path)
    audit_scope = {
        "framework": "Earlier framework observation",
        "framework_source": "Earlier observation source",
        "cases": ["refund"],
        "traces": ["selected.json"],
        "confirmed": True,
        "needs_review": False,
        "status": "in_progress",
        "selection_source": "Earlier user scope approval",
    }
    if existing_scope:
        state.update(tmp_path, {"scopes": {"audit": audit_scope}})
    original = state.initialize(tmp_path, journey="audit", state_only=True)
    observation = {"framework": "NeMo Gym", "framework_source": "User framework answer, reply 8"}
    patch = {
        "scopes": {"audit": observation},
        "prerequisites": {"data": {"status": "unknown", "source": "Completed-run trace availability is unresolved"}},
        "blockers": [{"id": "data", "phase": "setup", "message": "Confirm completed-run traces exist"}],
        "pending_question": "Are traces from completed evaluations available?",
        "next_handoff": "eval-author",
    }
    payload = tmp_path / ".eval-author/.working/framework-observation.json"
    payload.write_text(json.dumps(patch))
    forbid_artifact_checks(state, monkeypatch, artifacts)
    if via_cli:
        monkeypatch.setattr(
            sys,
            "argv",
            ["journey_state.py", "--repo", str(tmp_path), "--state-only", "update", "--input", str(payload)],
        )
        assert state.main() == 0
        output = json.loads(capsys.readouterr().out)
        assert output["artifact_checks_deferred"] is True and output["task_readiness"] is None
        saved = output["state"]
    else:
        saved = state.update(tmp_path, patch, state_only=True, expected_revision=original["revision"])
    assert saved["revision"] == original["revision"] + 1
    assert saved["scopes"]["audit"] == {**(audit_scope if existing_scope else {}), **observation}
    assert saved["scopes"]["create"] == original["scopes"]["create"]
    assert saved["ethos"] == original["ethos"]
    assert saved["tasks"] == original["tasks"]
    assert not state.checklist(saved, state_only=True)[0]["complete"]
    assert state.checklist(saved, state_only=True)[0]["blocked"]
    assert state.load_state(tmp_path, state_only=True) == saved


@pytest.mark.parametrize("extra", ["confirmed", "needs_review", "status", "cases", "traces", "corpus", "suite"])
def test_state_only_audit_framework_observation_cannot_launder_scope_approval(state, tmp_path, extra):
    original = state.initialize(tmp_path, agent="assistant", journey="audit", state_only=True)
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="State-only updates accept only audit framework"):
        state.update(
            tmp_path,
            {"scopes": {"audit": {"framework": "Harbor", "framework_source": "Probe evidence", extra: True}}},
            state_only=True,
        )
    assert state.state_path(tmp_path).read_bytes() == before
    assert state.load_state(tmp_path, state_only=True) == original


@pytest.mark.parametrize(
    "scopes",
    [
        {"audit": {"framework": "Harbor"}},
        {"audit": {"framework_source": "Probe evidence"}},
        {"audit": {"framework": "", "framework_source": "Probe evidence"}},
        {"audit": {"framework": "Harbor", "framework_source": "  "}},
        {"audit": {"framework": {"confirmed": True}, "framework_source": "Probe evidence"}},
        {"audit": {"framework": "Harbor", "framework_source": None}},
        {"audit": {"framework": "Harbor", "framework_source": "Probe evidence"}, "create": {"confirmed": True}},
        {"create": {"framework": "Harbor", "framework_source": "Probe evidence"}},
    ],
)
def test_state_only_audit_framework_observation_requires_both_text_fields(state, tmp_path, scopes):
    state.initialize(tmp_path, agent="assistant", journey="audit", state_only=True)
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="State-only updates|scopes.audit"):
        state.update(tmp_path, {"scopes": scopes}, state_only=True)
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("journey", [None, "create", "expand"])
def test_state_only_framework_observation_requires_current_audit_journey(state, tmp_path, journey):
    original = state.initialize(tmp_path, agent="assistant", journey=journey, setup="gym", state_only=True)
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="require the audit journey"):
        state.update(
            tmp_path,
            {"scopes": {"audit": {"framework": "Harbor", "framework_source": "Probe evidence"}}},
            state_only=True,
        )
    assert state.state_path(tmp_path).read_bytes() == before
    assert state.load_state(tmp_path, state_only=True) == original


def test_state_only_framework_observation_never_replaces_nonmapping_history(state, tmp_path):
    state.initialize(tmp_path, agent="assistant", journey="audit", state_only=True)
    original = state.update(tmp_path, {"scopes": {"audit": "Historical scope requiring review"}})
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="Saved audit scope must be a mapping"):
        state.update(
            tmp_path,
            {"scopes": {"audit": {"framework": "Harbor", "framework_source": "Probe evidence"}}},
            state_only=True,
        )
    assert state.state_path(tmp_path).read_bytes() == before
    assert state.load_state(tmp_path, state_only=True) == original
