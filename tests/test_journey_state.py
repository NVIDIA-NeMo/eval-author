# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise prerequisite evidence, user decisions, content binding, and safe resumption."""

import copy
import importlib
import json
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
        ("create", "Create your first evaluations"),
        ("audit", "Check what your existing evaluations cover"),
        ("expand", "Improve your existing evaluations"),
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


def test_resume_and_journey_switch_reuse_shared_confirmations(state, tmp_path):
    setup_ready(state, tmp_path)
    original = ethos_ready(state, tmp_path)
    state.update(
        tmp_path,
        {"scopes": {"create": {"cases": ["refund"], "confirmed": True}}, "pending_question": "Which case first?"},
    )
    resumed = state.initialize(tmp_path, agent="assistant", journey="audit", setup="gym")
    assert resumed["prerequisites"] == original["prerequisites"]
    assert resumed["ethos"] == original["ethos"]
    assert resumed["scopes"]["create"]["cases"] == ["refund"]
    assert resumed["pending_question"] is None
    assert state.checklist(resumed)[2]["label"] == "Check what your existing evaluations cover"


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
                "runtime": {"status": "verified", "source": "Gym import check succeeded"},
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
    assert "services" not in changed["prerequisites"]


@pytest.mark.parametrize("change", ["edit", "delete", "symlink"])
def test_changed_ethos_loses_approval_without_mutating_on_show(state, tmp_path, change):
    setup_ready(state, tmp_path)
    ethos_ready(state, tmp_path)
    state.update(tmp_path, {"scopes": {"audit": {"traces": ["selected.json"], "confirmed": True}}})
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


def test_blocked_setup_allows_independent_ethos_but_not_completion(state, tmp_path):
    setup_ready(state, tmp_path)
    result = state.update(
        tmp_path,
        {
            "blockers": [{"id": "runtime", "phase": "setup", "message": "Runtime missing"}],
            "journey_status": {"create": "complete"},
        },
    )
    rows = state.checklist(result)
    assert rows[0]["blocked"] and not rows[0]["complete"]
    assert rows[1]["current"]
    assert not rows[2]["complete"]


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
