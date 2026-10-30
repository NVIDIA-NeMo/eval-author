# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import importlib
import json
import shutil
from pathlib import Path

import pytest
from test_audit_binding import measured as native_measurement
from test_audit_state import reviewed, selected
from test_gym_to_atif import rollout
from test_journey_state import ethos_ready, setup_ready
from test_skill_contract import _AUDIT_REPORT, _MLFLOW_TO_ATIF, _mlflow_export, _run_json_script, _write_atif_trace

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("journey_state"), importlib.import_module("audit_state")


def ready(modules, repo):
    state, _ = modules
    setup_ready(state, repo, journey="audit", setup=None)
    return ethos_ready(state, repo)


def forbidden(*_args, **_kwargs):
    raise AssertionError("An unrelated workflow's evidence must not be inspected")


def start_lifecycle(modules, repo):
    state, audit = modules
    saved = ready(modules, repo)
    return state.update(
        repo,
        {
            "scopes": {
                "audit": {
                    "framework": "NAT",
                    "framework_source": "Selected evaluation documentation",
                    "legacy_context": {"priorities": ["refunds"]},
                    "lifecycle": audit.new_record(),
                }
            }
        },
        expected_revision=saved["revision"],
    )


def test_typed_audit_updates_require_current_revision_and_preserve_scope_siblings(modules, tmp_path):
    state, audit = modules
    saved = start_lifecycle(modules, tmp_path)
    lifecycle = copy.deepcopy(audit.record(saved))
    lifecycle["suites"]["candidates"] = ["evals/refunds"]
    patch = {"scopes": {"audit": {"lifecycle": lifecycle}}}
    original = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="expected-revision"):
        state.update(tmp_path, patch)
    with pytest.raises(ValueError, match="State changed"):
        state.update(tmp_path, patch, expected_revision=saved["revision"] - 1)
    assert state.state_path(tmp_path).read_bytes() == original
    updated = state.update(tmp_path, patch, expected_revision=saved["revision"])
    assert updated["scopes"]["audit"] == {**saved["scopes"]["audit"], "lifecycle": lifecycle}
    observed = state.update(
        tmp_path,
        {"scopes": {"audit": {"framework": "NAT / custom runner", "framework_source": "Current README"}}},
        expected_revision=updated["revision"],
    )
    assert audit.record(observed) == lifecycle
    assert observed["scopes"]["audit"]["legacy_context"] == saved["scopes"]["audit"]["legacy_context"]


@pytest.mark.parametrize("replacement", [None, "legacy", {"lifecycle": None}, {"lifecycle": {"pending": None}}])
def test_typed_audit_cannot_be_dropped_or_replaced_with_an_incomplete_record(modules, tmp_path, replacement):
    state, _ = modules
    saved = start_lifecycle(modules, tmp_path)
    original = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        state.update(tmp_path, {"scopes": {"audit": replacement}}, expected_revision=saved["revision"])
    assert state.state_path(tmp_path).read_bytes() == original


@pytest.mark.parametrize(
    "key,field,unselected", [("suites", "suite_files", "none"), ("traces", "trace_files", "unavailable")]
)
@pytest.mark.parametrize("change", ["pending", "unselected", "outside"])
def test_inconsistent_audit_sources_are_rejected_before_saving(
    modules, tmp_path, monkeypatch, key, field, unselected, change
):
    state, audit = modules
    saved = start_lifecycle(modules, tmp_path)
    _, lifecycle, _ = selected(audit)
    if change == "outside":
        lifecycle[field] = [lifecycle[key]["paths"][0] + "-other/evidence.json"]
    else:
        lifecycle[key]["status"] = "pending" if change == "pending" else unselected
        lifecycle[key]["paths"] = []
        lifecycle[key]["source"] = None if change == "pending" else "User ruled out this source"
    original = state.state_path(tmp_path).read_bytes()
    monkeypatch.setattr(audit, "observe", forbidden)
    with pytest.raises(ValueError, match=field):
        state.update(
            tmp_path,
            {"scopes": {"audit": {"lifecycle": lifecycle}}},
            expected_revision=saved["revision"],
        )
    assert state.state_path(tmp_path).read_bytes() == original


@pytest.mark.parametrize(
    "change,stale",
    [("question", False), ("scope", False), ("unchanged", False)]
    + [(change, stale) for change in ("completion", "review_and_completion") for stale in (False, True)],
)
def test_audit_updates_hash_evidence_only_when_needed(modules, tmp_path, monkeypatch, change, stale):
    state, audit = modules
    saved = ready(modules, tmp_path)
    _, lifecycle, observed = reviewed(audit)
    saved["scopes"]["audit"] = {"lifecycle": lifecycle}
    digest = audit.derive(saved, observed)["checks_digest"]
    lifecycle["checks_approval"]["digest"] = lifecycle["measurement"]["checks_digest"] = digest
    lifecycle["findings_review"]["digest"] = audit.derive(saved, observed)["findings_digest"]
    state.save(tmp_path, saved)
    original = state.state_path(tmp_path).read_bytes()
    patch = {}
    if change == "question":
        patch["pending_question"] = "Which next step would you like?"
    elif change == "scope":
        patch["scopes"] = {"audit": {"framework": "Harbor", "framework_source": "User answer"}}
    elif change == "unchanged":
        patch["scopes"] = {"audit": {"lifecycle": copy.deepcopy(lifecycle)}}
    else:
        patch["journey_status"] = {"audit": "complete"}
        if change == "review_and_completion":
            lifecycle = copy.deepcopy(lifecycle)
            lifecycle["findings_review"]["source"] = "User confirmed the current findings"
            patch["scopes"] = {"audit": {"lifecycle": lifecycle}}
    if stale:
        observed["files"][lifecycle["trace_files"][0]] = audit.fingerprint("changed trace")
    reads = []
    file_digest = state._file_digest

    def read(path, remaining):
        name = path.relative_to(tmp_path).as_posix()
        if name not in observed["files"]:
            return file_digest(path, remaining)
        reads.append(name)
        return observed["files"][name], 1

    monkeypatch.setattr(state, "_file_digest", read)
    if stale:
        with pytest.raises(ValueError, match="current"):
            state.update(tmp_path, patch, expected_revision=saved["revision"])
        assert state.state_path(tmp_path).read_bytes() == original
    else:
        updated = state.update(tmp_path, patch, expected_revision=saved["revision"])
        if "completion" in change:
            assert updated["journey_status"]["audit"] == "complete"
    expected = sorted(observed["files"]) if "completion" in change else []
    assert sorted(reads) == expected


@pytest.mark.parametrize("journey", ["create", "expand"])
@pytest.mark.parametrize("existing", [False, True])
def test_authoring_preserves_audit_without_reading_or_changing_it(modules, tmp_path, monkeypatch, journey, existing):
    state, audit = modules
    (start_lifecycle if existing else ready)(modules, tmp_path)
    saved = state.initialize(tmp_path, journey=journey, setup="harbor")
    saved["journey_status"][journey] = "complete"
    state.save(tmp_path, saved)
    saved = state.load_state(tmp_path)
    lifecycle = copy.deepcopy(audit.record(saved)) if existing else audit.new_record()
    lifecycle["suites"]["candidates"] = ["evals/refunds"]
    monkeypatch.setattr(audit, "observe", forbidden)
    monkeypatch.setattr(audit, "derive", forbidden)
    output = state.view(saved)
    assert output["audit"] is None and output["audit_observations"] is None
    original = state.state_path(tmp_path).read_bytes()
    scope = {"scopes": {"audit": {"lifecycle": lifecycle}}}
    for patch in (
        scope,
        {"journey_status": {"audit": "in_progress"}},
        {**scope, "journey_status": {"audit": "complete"}},
    ):
        with pytest.raises(ValueError, match="Select the audit journey"):
            state.update(tmp_path, patch, expected_revision=saved["revision"])
    assert state.state_path(tmp_path).read_bytes() == original
    assert state.load_state(tmp_path)["journey_status"][journey] == "complete"


@pytest.mark.parametrize("stage", ["suite_selection", "findings_review", "next_action"])
def test_domain_pending_checkpoint_survives_switch_and_resumes_without_reselection(
    modules, tmp_path, monkeypatch, stage
):
    state, audit = modules
    saved = start_lifecycle(modules, tmp_path)
    if stage == "suite_selection":
        lifecycle = copy.deepcopy(audit.record(saved))
        lifecycle["suites"]["candidates"] = ["evals/refunds"]
        lifecycle["traces"] = selected(audit)[1]["traces"]
        observed = {"files": {}}
    else:
        _, lifecycle, observed = reviewed(audit)
        saved["scopes"]["audit"]["lifecycle"] = lifecycle
        lifecycle["measurement"]["status"] = "partial"
        digest = audit.derive(saved, observed)["checks_digest"]
        lifecycle["checks_approval"]["digest"] = lifecycle["measurement"]["checks_digest"] = digest
        lifecycle["findings_review"]["digest"] = audit.derive(saved, observed)["findings_digest"]
        if stage == "findings_review":
            lifecycle["findings_review"] = None
        state.save(tmp_path, saved)
    trace_choice = copy.deepcopy(lifecycle["traces"])
    saved["scopes"]["audit"]["lifecycle"] = lifecycle
    monkeypatch.setattr(audit, "observe", lambda *_: observed)
    lifecycle["pending"] = {
        "stage": stage,
        "digest": audit.derive(saved, observed)["checkpoint_digests"][stage],
        "question": f"Please resolve {stage}",
        "source": "Presented the current audit decision",
    }
    state.update(
        tmp_path,
        {"scopes": {"audit": {"lifecycle": lifecycle}}, "pending_question": lifecycle["pending"]["question"]},
        expected_revision=saved["revision"],
    )
    switched = state.initialize(tmp_path, journey="create", setup="harbor")
    assert switched["pending_question"] is None
    assert audit.record(switched)["traces"] == trace_choice
    resumed = state.initialize(tmp_path, journey="audit")
    resumed = state.update(tmp_path, {"prerequisites": saved["prerequisites"]}, expected_revision=resumed["revision"])
    output = state.view(resumed)
    assert audit.record(resumed) == lifecycle
    assert output["audit"]["stage"] == stage
    assert output["audit"]["pending_checkpoint"] == lifecycle["pending"]
    assert output["state"]["pending_question"] == lifecycle["pending"]["question"]
    assert not output["audit"]["complete"] and not output["checklist"][2]["complete"]


def test_state_only_update_and_view_preserve_typed_audit_without_artifact_reads(modules, tmp_path, monkeypatch):
    state, audit = modules
    saved = start_lifecycle(modules, tmp_path)
    monkeypatch.setattr(audit, "observe", forbidden)
    monkeypatch.setattr(audit, "derive", forbidden)
    monkeypatch.setattr(audit, "transition", forbidden)
    monkeypatch.setattr(state.first_eval_state, "observe", forbidden)
    monkeypatch.setattr(state.first_eval_state, "derive", forbidden)
    monkeypatch.setattr(state, "ethos_digest", forbidden)
    updated = state.update(
        tmp_path,
        {"scopes": {"audit": {"framework": "Harbor", "framework_source": "User framework answer"}}},
        state_only=True,
        expected_revision=saved["revision"],
    )
    assert audit.record(updated) == audit.record(saved)
    assert updated["scopes"]["audit"]["legacy_context"] == saved["scopes"]["audit"]["legacy_context"]
    output = state.view(state.load_state(tmp_path, state_only=True), state_only=True)
    for field in ("audit", "audit_observations", "first_eval", "first_eval_observations", "task_readiness"):
        assert output[field] is None


def test_audit_completion_without_scope_patch_still_requires_revision_and_typed_proof(modules, tmp_path):
    state, _ = modules
    saved = ready(modules, tmp_path)
    before = state.state_path(tmp_path).read_bytes()
    patch = {"journey_status": {"audit": "complete"}}
    with pytest.raises(ValueError, match="expected-revision"):
        state.update(tmp_path, patch)
    with pytest.raises(ValueError, match="State changed"):
        state.update(tmp_path, patch, expected_revision=0)
    with pytest.raises(ValueError, match="Audit completion"):
        state.update(tmp_path, patch, expected_revision=saved["revision"])
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("journey", ["create", "expand"])
@pytest.mark.parametrize("field", ["scopes", "journey_status"])
def test_audit_updates_cannot_change_authoring_progress(modules, tmp_path, journey, field):
    state, _ = modules
    saved = ready(modules, tmp_path)
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="Select an authoring journey"):
        state.update(
            tmp_path,
            {field: {journey: "in_progress" if field == "journey_status" else {"confirmed": False}}},
            expected_revision=saved["revision"],
        )
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize(
    "scope",
    [
        pytest.param(..., id="absent"),
        pytest.param({}, id="empty_object"),
        pytest.param({"confirmed": True, "cases": ["refund"]}, id="object"),
        pytest.param([], id="empty_array"),
        pytest.param(["refund", {"source": "retained decision"}], id="array"),
        pytest.param("", id="empty_string"),
        pytest.param("Historical refund scope", id="string"),
        pytest.param(0, id="zero"),
        pytest.param(2, id="integer"),
        pytest.param(1.5, id="number"),
        pytest.param(False, id="false"),
        pytest.param(True, id="true"),
        pytest.param(None, id="null"),
    ],
)
@pytest.mark.parametrize("return_journey", ["create", "expand"])
def test_audit_ethos_change_preserves_prior_authoring_until_return(
    modules, tmp_path, monkeypatch, scope, return_journey
):
    state, _ = modules
    saved = ready(modules, tmp_path)
    for journey in ("create", "expand"):
        if scope is not Ellipsis:
            saved["scopes"][journey] = copy.deepcopy(scope)
        saved["journey_status"][journey] = "complete"
    state.save(tmp_path, saved)
    for journey in (return_journey, "audit"):
        unchanged = state.initialize(tmp_path, journey=journey, setup="harbor")
        assert unchanged["scopes"] == saved["scopes"]
        assert unchanged["journey_status"] == saved["journey_status"]
    before = state.state_path(tmp_path).read_bytes()
    expected_scope = {} if scope is Ellipsis else copy.deepcopy(scope)
    if not isinstance(expected_scope, dict):
        expected_scope = {"legacy_context": expected_scope}
    expected_scope.update(needs_review=True, confirmed=False)
    with monkeypatch.context() as patch:
        patch.setattr(state.first_eval_state, "observe", forbidden)
        patch.setattr(state.first_eval_state, "progress", forbidden)
        patch.setattr(state, "task_lifecycle", forbidden)
        (tmp_path / "ETHOS.md").write_text("# Changed support intent\n")
        loaded = state.load_state(tmp_path)
        output = state.view(loaded)
        assert output["first_eval"] is None and output["task_readiness"] is None
        assert loaded["ethos"]["stage"] == "review"
        assert state.state_path(tmp_path).read_bytes() == before
        for journey in ("create", "expand"):
            assert loaded["journey_status"][journey] == "complete"
            assert loaded["scopes"][journey] == expected_scope
        invalidated = copy.deepcopy(loaded)
        state.invalidate_scopes(loaded)
        assert loaded == invalidated
        state.initialize(tmp_path, journey="audit")
        confirmed = state.confirm_ethos(
            tmp_path,
            reviewed_digest=state.ethos_digest(tmp_path),
            source="User reviewed the changed intent",
            validation="Updated Ethos passed validation",
        )
        assert confirmed["journey_status"] == {"create": "complete", "expand": "complete"}
        assert confirmed["scopes"] == loaded["scopes"]
    returned = state.initialize(tmp_path, journey=return_journey, setup="harbor")
    assert returned["journey_status"] == {"create": "in_progress", "expand": "in_progress"}
    assert not state.checklist(returned)[2]["complete"]
    assert returned["scopes"] == loaded["scopes"]


@pytest.mark.parametrize("change", ["changed", "missing"])
def test_ethos_review_pauses_audit_evidence_reads_until_reconfirmed(modules, tmp_path, monkeypatch, change):
    state, audit = modules
    saved = ready(modules, tmp_path)
    _, lifecycle, observed = reviewed(audit)
    saved["scopes"]["audit"] = {"lifecycle": lifecycle}
    saved["journey_status"]["audit"] = "complete"
    digest = audit.derive(saved, observed)["checks_digest"]
    lifecycle["checks_approval"]["digest"] = lifecycle["measurement"]["checks_digest"] = digest
    digest = audit.derive(saved, observed)["findings_digest"]
    lifecycle["findings_review"]["digest"] = digest
    lifecycle["pending"] = {
        "stage": "next_action",
        "digest": digest,
        "question": "Would you like improvement proposals?",
        "source": "User reviewed the findings",
    }
    state.save(tmp_path, saved)
    reads = []
    allowed = True
    file_digest = state._file_digest

    def read(path, remaining):
        name = path.relative_to(tmp_path).as_posix()
        if name not in observed["files"]:
            return file_digest(path, remaining)
        assert allowed, "Audit evidence must not be read during Ethos review"
        reads.append(name)
        return observed["files"][name], 1

    monkeypatch.setattr(state, "_file_digest", read)
    assert state.view(state.load_state(tmp_path))["checklist"][2]["complete"]
    assert set(reads) == set(observed["files"])
    reads.clear()
    allowed = False
    ethos = tmp_path / "ETHOS.md"
    if change == "changed":
        ethos.write_text("# Changed intent\n")
    else:
        ethos.unlink()
    before = state.state_path(tmp_path).read_bytes()
    viewed = state.view(state.load_state(tmp_path))
    assert state.state_path(tmp_path).read_bytes() == before
    updated = state.update(
        tmp_path,
        {"scopes": {"audit": {"framework": "Harbor", "framework_source": "User framework answer"}}},
        expected_revision=viewed["state"]["revision"],
    )
    for output in (viewed, state.view(updated)):
        assert output["state"]["ethos"]["stage"] == "review"
        assert (
            output["state"]["pending_question"]
            == "ETHOS.md changed or is unavailable; review its intended behavior again."
        )
        assert output["checklist"][1]["current"] and not output["checklist"][2]["complete"]
        assert output["audit_observations"] == {"files": {}}
        assert audit.record(output["state"]) == lifecycle
    assert not reads
    ethos.write_text("# Reviewed updated intent\n")
    assert state.view(state.load_state(tmp_path))["audit_observations"] == {"files": {}}
    allowed = True
    confirmed = state.confirm_ethos(
        tmp_path,
        reviewed_digest=state.ethos_digest(tmp_path),
        source="User reconfirmed the updated intent",
        validation="Updated Ethos passed validation",
    )
    state.view(confirmed)
    assert set(reads) == set(observed["files"])


@pytest.mark.parametrize("relocation", ["move", "copy"])
def test_rebind_reopens_audit_approval_after_unchanged_ethos_is_reconfirmed(modules, tmp_path, relocation):
    state, audit = modules
    original = tmp_path / "original"
    original.mkdir()
    saved = ready(modules, original)
    _, lifecycle, observed = reviewed(audit)
    saved["scopes"]["audit"] = {"lifecycle": lifecycle}
    saved["journey_status"]["audit"] = "complete"
    for name in observed["files"]:
        path = original / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    observed = audit.observe(original, saved)
    for ref in lifecycle["measurement"]["evidence"] + [lifecycle["findings"]["report"]]:
        ref["digest"] = observed["files"][ref["path"]]
    digest = audit.derive(saved, observed)["checks_digest"]
    lifecycle["checks_approval"]["digest"] = lifecycle["measurement"]["checks_digest"] = digest
    lifecycle["findings_review"]["digest"] = audit.derive(saved, observed)["findings_digest"]
    state.save(original, saved)
    assert state.view(state.load_state(original))["checklist"][2]["complete"]
    evidence = {name: (original / name).read_bytes() for name in [*observed["files"], "ETHOS.md"]}

    relocated = tmp_path / "relocated"
    if relocation == "move":
        original.rename(relocated)
    else:
        shutil.copytree(original, relocated)
    rebound = state.rebind(relocated, previous_repository=str(original), expected_revision=saved["revision"])
    state.update(relocated, {"prerequisites": saved["prerequisites"]}, expected_revision=rebound["revision"])
    state.confirm_ethos(
        relocated,
        reviewed_digest=saved["ethos"]["confirmed_digest"],
        source="User reconfirmed unchanged intent in the relocated repository",
        validation="Unchanged Ethos passed validation",
    )
    resumed = state.view(state.load_state(relocated))
    assert all(row["complete"] for row in resumed["checklist"][:2])
    assert resumed["audit"]["stage"] == "checks_review"
    assert not any(
        resumed["audit"][field]
        for field in ("checks_approved", "measurement_complete", "findings_reviewed", "complete")
    )
    assert not resumed["checklist"][2]["complete"]
    assert audit.record(resumed["state"]) == lifecycle
    assert resumed["audit_observations"] == observed
    assert all((relocated / name).read_bytes() == contents for name, contents in evidence.items())


@pytest.mark.parametrize(
    "format,scenario",
    [
        (format, scenario)
        for format in ("atif", "gym", "mlflow")
        for scenario in ("mapped", "ambiguous", "shared_output")
    ]
    + [("gym", "failed_selection")],
)
def test_native_results_resume_and_changed_trace_reopens_audit(modules, tmp_path, format, scenario):
    state, audit = modules
    _, lifecycle, _ = selected(audit)
    source = tmp_path / "runs" / ("source.jsonl" if format == "gym" else "source.json")
    source.parent.mkdir()
    selector = None
    prepared = tmp_path / "prepared"
    failed = None
    if format == "atif":
        _write_atif_trace(source, tool_calls=["customer.lookup"])
        trace = source
    else:
        if format == "gym":
            source.write_text("invalid unselected row\n" + json.dumps(rollout()) + "\n")
            if scenario == "failed_selection":
                source.write_text(source.read_text() + "{}\n")
            selector = 2
            converter = SCRIPTS.parents[1] / "gym-to-atif/scripts/gym_to_atif.py"
            arguments = ["--input", str(source), "--row", str(selector)]
        else:
            payload = _mlflow_export()
            payload["traces"].append({"info": {"trace_id": "unselected"}, "data": "invalid unselected trace"})
            source.write_text(json.dumps(payload))
            selector = "tr-fixture"
            selected_trace = tmp_path / "selected.json"
            selected_trace.write_text(
                json.dumps(next(t for t in payload["traces"] if t["info"]["trace_id"] == selector))
            )
            converter = _MLFLOW_TO_ATIF
            arguments = ["--input", str(selected_trace), "--agent-name", "example-agent"]
        code, result, stderr = _run_json_script(converter, *arguments, "--output-dir", str(prepared))
        assert code == 0, stderr or result
        trace = prepared / "trace.atif.json" if format == "gym" else Path(result["files"][0])
        if scenario == "failed_selection":
            code, failed, stderr = _run_json_script(
                converter, "--input", str(source), "--row", "3", "--output-dir", str(prepared / "failed")
            )
            assert code == 2 and failed["status"] == "error", stderr or failed
            assert not (prepared / "failed/trace.atif.json").exists()
    selection = {
        "path": source.relative_to(tmp_path).as_posix(),
        "source_digest": state.digest(source.read_bytes()),
        "format": format,
        "selector": selector,
        "suite": lifecycle["suites"]["paths"][0],
        "task_id": "fixture",
        "run_id": "selected-suite-run",
        "mapping_source": "User explicitly mapped this record to the selected suite task and run",
        "evidence_class": "agent_under_test",
    }
    specification, trace, judgments, aggregate = native_measurement(
        tmp_path, trace=trace, run_id=selection["run_id"], include_unrelated=scenario == "shared_output"
    )
    aggregate_payload = json.loads(aggregate.read_text())
    coverage_paths = [Path(row["path"]) for row in aggregate_payload["binding"]["inputs"]]
    assert len(coverage_paths) == len(aggregate_payload["input_reports"]) == 2
    for path in coverage_paths:
        subject = json.loads(path.read_text())["subject"]
        assert all(subject[key] == selection[key] for key in ("task_id", "run_id", "evidence_class"))
    state.initialize(tmp_path, agent="example-agent", journey="audit")
    state.update(
        tmp_path,
        {"prerequisites": {key: {"status": "confirmed", "source": "Local fixture"} for key in state.PREREQUISITES}},
    )
    state.confirm_ethos(
        tmp_path,
        reviewed_digest=state.ethos_digest(tmp_path),
        source="User confirmed the native fixture intent",
        validation="Valid fixture",
    )
    for name in lifecycle["suite_files"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture evaluation")
    lifecycle["traces"]["paths"] = lifecycle["trace_files"] = [source.relative_to(tmp_path).as_posix()]
    lifecycle["traces"]["candidates"] = ["runs"]
    lifecycle["traces"]["exclusions"] = [
        {"path": "runs/excluded", "reason": "Unrelated source records", "source": "User excluded these records"}
    ]
    lifecycle["specification"] = specification.relative_to(tmp_path).as_posix()
    manifest = tmp_path / ".eval-author/selection.json"
    selections = [selection]
    if failed:
        selections.append({**selection, "selector": 3})
    manifest.write_text(json.dumps({"selections": selections}))
    lifecycle["selection_manifest"] = manifest.relative_to(tmp_path).as_posix()

    def save(**extra):
        saved = state.update(
            tmp_path,
            {"scopes": {"audit": {"lifecycle": copy.deepcopy(lifecycle)}}, **extra},
            expected_revision=state.load_state(tmp_path)["revision"],
        )
        return audit.progress(tmp_path, saved)

    def ref(path):
        return {"path": path.relative_to(tmp_path).as_posix(), "digest": state.digest(path.read_bytes())}

    if scenario == "ambiguous":
        chosen_traces = copy.deepcopy(lifecycle["traces"])
        lifecycle["traces"] = audit.new_record()["traces"]
        lifecycle["traces"]["candidates"] = lifecycle["trace_files"]
        lifecycle["trace_files"] = []
        unresolved = {
            **selection,
            "suite": None,
            "task_id": None,
            "run_id": None,
            "mapping_source": None,
            "mapping_candidates": [
                {key: selection[key] for key in ("suite", "task_id", "run_id")},
                {"suite": selection["suite"], "task_id": "other-fixture", "run_id": "other-suite-run"},
            ],
        }
        manifest.write_text(json.dumps({"selections": [unresolved]}))
        progress = save()
        assert progress["stage"] == "trace_selection" and progress["checks_digest"] is None
        lifecycle["pending"] = {
            "stage": "trace_selection",
            "digest": progress["checkpoint_digests"]["trace_selection"],
            "question": "Which suite task and run produced this trace?",
            "source": "The selected record cannot distinguish the two candidate runs",
        }
        save()
        before = state.state_path(tmp_path).read_bytes()
        unresolved_bytes = manifest.read_bytes()
        resumed = state.view(state.load_state(tmp_path))
        assert resumed["audit"]["pending_checkpoint"] == lifecycle["pending"]
        assert not resumed["audit"]["checks_approved"] and not resumed["audit"]["complete"]
        assert audit.record(resumed["state"])["trace_files"] == []
        assert audit.record(resumed["state"])["traces"] == lifecycle["traces"]
        with pytest.raises(ValueError, match="Audit completion"):
            state.update(
                tmp_path, {"journey_status": {"audit": "complete"}}, expected_revision=resumed["state"]["revision"]
            )
        assert state.state_path(tmp_path).read_bytes() == before and manifest.read_bytes() == unresolved_bytes
        lifecycle["traces"] = chosen_traces
        lifecycle["trace_files"] = [selection["path"]]
        lifecycle["pending"] = None
        manifest.write_text(json.dumps({"selections": selections}))

    progress = save()
    lifecycle["checks_approval"] = {"digest": progress["checks_digest"], "source": "User approved these checks"}
    assert save()["stage"] == "measurement"
    outcomes = tmp_path / ".eval-author/preparation-outcomes.json"
    outcomes.write_text(
        json.dumps(
            {
                "selections": [
                    {"selector": selector, "status": "complete", "prepared": ref(trace)},
                    *([{"selector": 3, "status": "failed", "conversion": failed}] if failed else []),
                ]
            }
        )
    )
    lifecycle["measurement"] = {
        "checks_digest": lifecycle["checks_approval"]["digest"],
        "status": "partial" if failed else "complete",
        "evidence": [
            ref(path)
            for path in [
                *sorted(prepared.glob("*.json")),
                *coverage_paths,
                *(path.with_name("details.json") for path in coverage_paths),
                judgments,
                outcomes,
                aggregate,
            ]
        ],
        "source": "Selected Gym row 3 failed conversion"
        if failed
        else "Native measurement and aggregate verification succeeded",
    }
    assert save()["measurement_complete"] is (not failed)
    report = tmp_path / ".eval-author/audit-coverage-report.md"
    report.write_text("# Findings\n\nLookup is exercised; unjudged behavior limits the findings.\n")
    lifecycle["findings"] = {"report": ref(report), "source": "Presented findings and evidence limits"}
    progress = save()
    lifecycle["findings_review"] = {"digest": progress["findings_digest"], "source": "User accepted the findings only"}
    if failed:
        progress = save()
        assert progress["findings_reviewed"] and not progress["complete"]
        saved = state.load_state(tmp_path)
        with pytest.raises(ValueError, match="Audit completion"):
            state.update(tmp_path, {"journey_status": {"audit": "complete"}}, expected_revision=saved["revision"])
        assert audit.progress(tmp_path, state.load_state(tmp_path))["stage"] == "measurement"
        assert json.loads(outcomes.read_text())["selections"][1]["conversion"] == failed
        return
    save(journey_status={"audit": "complete"})

    if scenario == "shared_output":
        all_coverage = set((tmp_path / "measurements").rglob("coverage.json"))
        unrelated = all_coverage - set(coverage_paths)
        assert len(unrelated) == 1
        unrelated_path = unrelated.pop()
        assert unrelated_path.relative_to(tmp_path).as_posix() not in {
            row["path"] for row in lifecycle["measurement"]["evidence"]
        }
        unrelated_path.write_text(unrelated_path.read_text() + "\n")
        code, result, stderr = _run_json_script(_AUDIT_REPORT, "--verify-existing", str(aggregate))
        assert code == 0, stderr or result
        assert state.view(state.load_state(tmp_path))["audit"]["complete"]

    for changed in [None, source, manifest, *sorted(prepared.glob("*.json")), outcomes, aggregate]:
        expected_stage = (
            "next_action" if changed is None else "checks_review" if changed in (source, manifest) else "measurement"
        )
        original = changed.read_bytes() if changed else b""
        if changed:
            changed.write_bytes(original + b"\n")
        before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
        resumed = state.view(state.load_state(tmp_path))
        assert resumed["audit"]["stage"] == expected_stage
        assert resumed["audit"]["checks_approved"] is (expected_stage != "checks_review")
        assert resumed["audit"]["complete"] is (not changed)
        assert resumed["checklist"][2]["complete"] is (not changed)
        assert not resumed["audit"]["next_action_current"]
        assert audit.record(resumed["state"]) == lifecycle
        assert all(path.read_bytes() == contents for path, contents in before.items())
        if changed:
            changed.write_bytes(original)
