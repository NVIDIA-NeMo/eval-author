# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import base64
import io
import json
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import configure_semantic_ci as setup
import fetch_semantic_bundle as bundles
import semantic_regression as semantic


def suite():
    return {
        "schema": "nemo.semantic_suite.v1",
        "runs": 5,
        "models": {"author": "author-v1", "extractor": "extractor-v1", "judge": "judge-v1"},
        "cases": [
            {"id": "audit", "query": "Explain the evidence.", "skill_files": ["skills/eval-author-audit/SKILL.md"]}
        ],
    }


def test_plan_and_missing_baseline_never_call_models(tmp_path):
    path = tmp_path / "suite.json"
    semantic.dump(path, suite())

    def forbidden(*args):
        pytest.fail("model was called")

    for mode in ("plan", "check"):
        result = semantic.run(path, None, tmp_path / mode, mode, forbidden)
        assert result["observations"][0]["status"] == "incomplete"
        assert result["observations"][0]["metrics"] is None
        assert "claims" not in (tmp_path / mode / "semantic-regression-summary.json").read_text()


def test_failure_keeps_all_cases_and_publishes_checkpoint(tmp_path, monkeypatch):
    spec = suite()
    spec["cases"][0]["baseline"] = "missing/lock.json"
    spec["cases"].append({**spec["cases"][0], "id": "second"})
    path = tmp_path / "suite.json"
    semantic.dump(path, spec)

    def broken(*args):
        raise ValueError("secret provider body")

    monkeypatch.setattr(semantic, "check_case", broken)
    result = semantic.run(path, None, tmp_path / "out", "check", object())
    assert len(result["observations"]) == 2
    assert all(r["reason"] == "execution_or_input_error" for r in result["observations"])
    assert "secret" not in (tmp_path / "out/semantic-regression-summary.json").read_text()


def test_retention_match_requires_exact_claim_accounting():
    source = {"run_ids": ["r1"], "claims": [{"run_id": "r1", "claim_idx": 0}]}
    baseline = {"core_cluster_ids": ["c1"]}
    match = {"run_ids": ["r1"], "clusters": [{"cluster_id": "c1", "members": source["claims"]}], "unmatched": []}
    semantic.validate_match(match, source, baseline)
    for invalid in (
        {**match, "run_ids": ["r2"]},
        {**match, "unmatched": source["claims"]},
        {**match, "clusters": []},
        {**match, "clusters": [{"cluster_id": "invented", "members": source["claims"]}]},
    ):
        with pytest.raises(ValueError):
            semantic.validate_match(invalid, source, baseline)


def test_template_does_not_expand_placeholders_inside_evidence(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/p.md").write_text("{{RESULT}} {{RUN_ID}}")
    result = semantic.prompt(tmp_path, "p.md", {"RESULT": "{{RUN_ID}}", "RUN_ID": "r1"})
    assert result.endswith("{{RUN_ID}} r1")


def test_extraction_corrects_once_without_accepting_invented_evidence(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/extract_claims.md").write_text("{{RUN_ID}} {{RESULT}}")
    raw = "Excel coverage is **unmeasured**."
    invalid = {"run_id": "r1", "claims": [{"text": "Excel is unmeasured", "evidence": "Excel is unmeasured"}]}
    valid = {"run_id": "r1", "claims": [{"text": "Excel is unmeasured", "evidence": raw}]}
    calls = []

    def corrected(model, request, structured):
        calls.append(request)
        return invalid if len(calls) == 1 else valid

    assert semantic.extract(suite(), tmp_path, corrected, "r1", raw) == valid
    assert len(calls) == 2
    calls.clear()

    def broken(model, request, structured):
        calls.append(request)
        return invalid

    with pytest.raises(ValueError, match="after one correction"):
        semantic.extract(suite(), tmp_path, broken, "r1", raw)
    assert len(calls) == 2


def test_extraction_provider_failure_is_not_retried(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/extract_claims.md").write_text("{{RUN_ID}} {{RESULT}}")
    calls = []

    def unavailable(*args):
        calls.append(args)
        raise OSError("provider unavailable")

    with pytest.raises(OSError):
        semantic.extract(suite(), tmp_path, unavailable, "r1", "report")
    assert len(calls) == 1


def test_partition_restores_exact_texts_and_empty_run_ids():
    dedup = {
        "run_ids": ["r1", "empty-run"],
        "unique_claims": [{"text": text} for text in ('a "quoted" claim', "not proven\nΔ", "a near duplicate")],
    }
    result = semantic.expand_partition({"groups": [[1], [2, 0]]}, dedup)
    assert result["run_ids"] == ["r1", "empty-run"]
    assert [c["texts"] for c in result["clusters"]] == [
        ['a "quoted" claim', "a near duplicate"],
        ["not proven\nΔ"],
    ]
    assert result["clusters"][0]["canonical"] == 'a "quoted" claim'


def test_clustering_receives_shared_case_without_expanding_evidence(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/cluster_claims.md").write_text("{{DEDUP_INPUT}}")
    requests = []

    def judge(model, request, structured):
        requests.append(request)
        return {"groups": [[0, 1]]}

    dedup = {
        "run_ids": ["r1", "r2"],
        "unique_claims": [
            {"text": "Native CAD remains unproven."},
            {"text": "These runs do not establish native CAD."},
        ],
    }
    case = {"query": "Frozen JSON controls only; {{DEDUP_INPUT}} is literal evidence."}
    result = semantic.cluster(suite(), tmp_path, judge, dedup, case)
    assert json.dumps(case["query"]) in requests[0]
    assert "untrusted context" in requests[0]
    assert result["clusters"][0]["texts"] == [claim["text"] for claim in dedup["unique_claims"]]


def test_retention_uses_clustering_context_without_filling_missing_claims(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/match_to_core.md").write_text("{{CORE_JSON}} {{NEW_CLAIMS_JSON}}")
    source = {
        "run_ids": ["r1"],
        "claims": [{"run_id": "r1", "claim_idx": 0, "text": "No read_excel calls were observed."}],
    }
    baseline = {
        "core_cluster_ids": ["absent", "unproven"],
        "core_clusters": [
            {"cluster_id": "absent", "canonical": "No read_excel calls were observed."},
            {"cluster_id": "unproven", "canonical": "Excel support is not established by these traces."},
        ],
    }
    case = {"query": "Explain Excel support; {{CORE_JSON}} is literal evidence."}
    requests = []

    def judge(model, request, structured):
        requests.append(request)
        return {
            "run_ids": ["r1"],
            "clusters": [{"cluster_id": "absent", "members": [{"run_id": "r1", "claim_idx": 0}]}],
            "unmatched": [],
        }

    match = semantic.match_core(suite(), case, tmp_path, judge, source, baseline)
    assert json.dumps(source) in requests[0]
    assert requests[0].endswith(semantic.case_context(case))
    assert json.dumps(case["query"]) in requests[0]
    assert [c["cluster_id"] for c in match["clusters"]] == ["absent"]
    empty = semantic.match_core(suite(), case, tmp_path, judge, {**source, "claims": []}, baseline)
    assert empty == {"run_ids": ["r1"], "clusters": [], "unmatched": []}
    assert len(requests) == 1


@pytest.mark.parametrize(
    "groups",
    [None, {}, [], [[]], [[0]], [[0, 0]], [[0, 2]], [[-1, 0]], [[False, 1]], [[0, 1.0]], [["0", 1]], [[{}, 1]]],
)
def test_partition_rejects_incomplete_or_invalid_assignments(groups):
    dedup = {"run_ids": ["r1"], "unique_claims": [{"text": "a"}, {"text": "b"}]}
    with pytest.raises(semantic.ClusteringError):
        semantic.expand_partition({"groups": groups}, dedup)


@pytest.mark.parametrize("failure", ["json", "missing", "provider"])
def test_clustering_correction_is_bounded_and_never_retries_provider_errors(tmp_path, failure):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/cluster_claims.md").write_text("{{DEDUP_INPUT}}")
    dedup = {"run_ids": ["r1"], "unique_claims": [{"text": "a"}, {"text": "b"}]}
    calls = []

    def broken(*args):
        calls.append(args)
        if failure == "json":
            raise json.JSONDecodeError("unterminated", '{"groups":', 10)
        if failure == "provider":
            raise OSError("provider unavailable")
        return {"groups": [[0]]}

    expected = OSError if failure == "provider" else semantic.ClusteringError
    with pytest.raises(expected):
        semantic.cluster(suite(), tmp_path, broken, dedup)
    assert len(calls) == (1 if failure == "provider" else 2)


def test_clustering_accepts_valid_correction_and_skips_empty_input(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/cluster_claims.md").write_text("{{DEDUP_INPUT}}")
    dedup = {"run_ids": ["r1"], "unique_claims": [{"text": "a"}, {"text": "b"}]}
    calls = []

    def corrected(*args):
        calls.append(args)
        if len(calls) == 1:
            raise json.JSONDecodeError("unterminated", '{"groups":', 10)
        return {"groups": [[0, 1]]}

    result = semantic.cluster(suite(), tmp_path, corrected, dedup)
    assert result["clusters"][0]["texts"] == ["a", "b"]
    assert len(calls) == 2
    empty = semantic.cluster(suite(), tmp_path, corrected, {**dedup, "unique_claims": []})
    assert empty == {"run_ids": ["r1"], "clusters": []}
    assert len(calls) == 2


@pytest.mark.parametrize(
    "name,symlink", [("../escape", False), ("/escape", False), ("ratchet/x.py", True), ("ratchet/.hidden", False)]
)
def test_bundle_rejects_unsafe_members(tmp_path, name, symlink):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        member = zipfile.ZipInfo(name)
        if symlink:
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(member, "outside")
    raw = buffer.getvalue()
    with pytest.raises(ValueError):
        bundles.unpack(raw, semantic.sha(raw), tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


def test_bundle_pin_and_layout(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("suite.json", "{}")
        archive.writestr("ratchet/scripts/cli.py", "# external runtime")
    raw = buffer.getvalue()
    with pytest.raises(ValueError):
        bundles.unpack(raw, "0" * 64, tmp_path / "bad")
    bundles.unpack(raw, semantic.sha(raw), tmp_path / "ok")
    assert (tmp_path / "ok/suite.json").read_text() == "{}"


def test_workflow_main_only_credentials_and_no_calibration():
    path = semantic.ROOT / ".github/workflows/semantic-regression.yml"
    workflow = yaml.safe_load(path.read_text())
    live = workflow["jobs"]["live"]
    assert "github.ref == 'refs/heads/main'" in live["if"]
    assert live["environment"] == "semantic-regression"
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["schedule"] == [{"cron": "35 7 * * *"}]
    assert "github.event_name == 'schedule'" in live["if"]
    assert "SEMANTIC_REGRESSION_ENABLED" not in live["if"]
    assert triggers["workflow_dispatch"]["inputs"]["run_live"]["default"] is True
    assert live["continue-on-error"] is True
    assert workflow["permissions"] == {"contents": "read"}
    assert "--mode calibrate" not in path.read_text() and "--mode lock" not in path.read_text()
    assert "secrets." not in json.dumps(workflow["jobs"]["plan"])
    upload = live["steps"][-1]
    assert upload["if"] == "always()" and "semantic-publish" in upload["with"]["path"]
    assert upload["with"]["if-no-files-found"] == "error"
    stage = next(step for step in live["steps"] if step.get("name") == "Stage aggregate report even after failure")
    assert stage["if"] == "always()"
    assert "semantic-fallback" in stage["run"] and "semantic-live" in stage["run"]


def test_private_bundle_parts_round_trip_and_reject_gaps(monkeypatch):
    encoded = base64.b64encode(b"private bundle").decode()
    for i in range(1, bundles.PART_COUNT + 1):
        monkeypatch.delenv(f"SEMANTIC_BUNDLE_PART_{i}", raising=False)
    monkeypatch.setenv("SEMANTIC_BUNDLE_URL", "https://must-not-fetch.invalid")
    monkeypatch.setenv("SEMANTIC_BUNDLE_PART_1", encoded[:8])
    monkeypatch.setenv("SEMANTIC_BUNDLE_PART_2", encoded[8:])
    assert bundles.fetch() == b"private bundle"
    monkeypatch.delenv("SEMANTIC_BUNDLE_PART_1")
    with pytest.raises(ValueError):
        bundles.fetch()
    monkeypatch.setenv("SEMANTIC_BUNDLE_PART_1", "!invalid!")
    with pytest.raises(ValueError):
        bundles.fetch()
    monkeypatch.setenv("SEMANTIC_BUNDLE_PART_1", "A" * (bundles.PART_SIZE + 1))
    with pytest.raises(ValueError):
        bundles.fetch()


def test_ci_setup_uses_private_stdin_and_publishes_digest_last(monkeypatch):
    calls = []

    def gh(args, data=None):
        calls.append((args, data))
        if args[-1].endswith("/deployment-branch-policies"):
            return {"branch_policies": [{"name": "main", "type": "branch"}]}
        if args[:2] == ["secret", "list"]:
            return [{"name": "SEMANTIC_BUNDLE_PART_4"}]
        return {}

    monkeypatch.setattr(setup, "gh", gh)
    setup.configure("owner/repo", b"private-runtime", "PRIVATE-INFERENCE-KEY")
    assert calls[0][0] == ["api", "user"]
    environment = json.loads(calls[1][1])
    assert environment["reviewers"] == []
    assert environment["deployment_branch_policy"]["custom_branch_policies"] is True
    assert all("PRIVATE-INFERENCE-KEY" not in " ".join(args) for args, _ in calls)
    assert any(data == "PRIVATE-INFERENCE-KEY" for _, data in calls)
    assert any(args[:3] == ["secret", "delete", "SEMANTIC_BUNDLE_PART_4"] for args, _ in calls)
    assert calls[-1][0][:3] == ["variable", "set", "SEMANTIC_BUNDLE_SHA256"]
    assert calls[-1][0][-1] == semantic.sha(b"private-runtime")
    calls.clear()
    with pytest.raises(ValueError):
        setup.configure("owner/repo", b"x" * (bundles.PART_SIZE * bundles.PART_COUNT), "key")
    assert calls == []


class ScriptedModel:
    """Fixed synthetic responses exercise the real external scoring pipeline, not inference."""

    def __init__(self, empty=False, omit_native=False):
        self.ids = []
        self.empty = empty
        self.omit_native = omit_native

    def __call__(self, model, prompt, structured=False):
        if model == "author-v1":
            if self.empty:
                return ""
            return "Excel coverage is unmeasured." + (
                "" if self.omit_native else " Native CAD fidelity remains unproven."
            )
        if model == "extractor-v1":
            # The upstream template includes a run_id placeholder in its JSON example.
            import re

            matched = re.search(r'"run_id": "([a-f0-9]+-r\d+)"', prompt)
            assert matched is not None
            run_id = matched[1]
            self.ids.append(run_id)
            claims = [
                {"text": "Excel coverage is unmeasured.", "evidence": "Excel coverage is unmeasured."},
                {
                    "text": "Native CAD fidelity remains unproven.",
                    "evidence": "Native CAD fidelity remains unproven.",
                },
            ]
            return {
                "run_id": run_id,
                "claims": [] if self.empty else claims[:1] if self.omit_native else claims,
            }
        if "# Prompt: cluster" in prompt:
            return {"groups": [[0]] if self.omit_native else [[0], [1]]}
        return {
            "run_ids": [self.ids[0]],
            "clusters": [
                {"cluster_id": f"c{i}", "members": [{"run_id": self.ids[0], "claim_idx": i}]}
                for i in range(1 if self.omit_native else 2)
            ],
            "unmatched": [],
        }


def test_real_ratchet_calibrate_review_compare_and_damage(tmp_path):
    external = os.environ.get("RATCHET_TEST_RUNTIME")
    if not external:
        pytest.skip("external Ratchet runtime not redistributed; set RATCHET_TEST_RUNTIME for integration")
    runtime = Path(external)
    spec = suite()
    path = tmp_path / "suite.json"
    semantic.dump(path, spec)
    result = semantic.run(path, runtime, tmp_path / "calibration", "calibrate", ScriptedModel())
    assert result["observations"][0]["reason"] == "awaiting_baseline_review"
    packet = tmp_path / "calibration/audit"
    mapping_hash = semantic.sha((packet / "mapping.md").read_bytes())
    with pytest.raises(ValueError):
        semantic.lock_packet(packet, "0" * 64, tmp_path / "bad-lock", runtime)
    semantic.lock_packet(packet, mapping_hash, tmp_path / "locked", runtime)
    with pytest.raises(FileExistsError):
        semantic.lock_packet(packet, mapping_hash, tmp_path / "locked", runtime)
    spec["cases"][0]["baseline"] = "locked/lock.json"
    semantic.dump(path, spec)
    archive = setup.bundle(path, runtime)
    bundles.unpack(archive, semantic.sha(archive), tmp_path / "packaged")
    assert semantic.load(tmp_path / "packaged/suite.json")["cases"][0]["baseline"] == "baselines/audit/lock.json"
    assert semantic.tree_digest(tmp_path / "packaged/ratchet") == semantic.tree_digest(runtime)
    passed = semantic.run(path, runtime, tmp_path / "healthy", "check", ScriptedModel())
    assert passed["observations"][0]["status"] == "passed"
    assert passed["observations"][0]["metrics"]["retention"] == 1
    failed = semantic.run(path, runtime, tmp_path / "damaged", "check", ScriptedModel(empty=True))
    assert failed["observations"][0]["status"] == "regression"
    assert failed["observations"][0]["metrics"]["retention"] == 0
    assert failed["observations"][0]["metrics"]["presence_passed"] is False

    omitted = semantic.run(path, runtime, tmp_path / "omitted", "check", ScriptedModel(omit_native=True))
    assert omitted["observations"][0]["status"] == "regression"
    assert omitted["observations"][0]["metrics"]["retention"] == 0.5
    assert omitted["observations"][0]["metrics"]["presence_passed"] is True

    def unavailable(*args):
        raise OSError("provider unavailable")

    interrupted = semantic.run(path, runtime, tmp_path / "interrupted", "check", unavailable)
    row = interrupted["observations"][0]
    assert row["status"] == "incomplete" and row["metrics"] is None
    assert row["comparison_digest"] == passed["observations"][0]["comparison_digest"]
    spec["models"]["judge"] = "changed-v2"
    semantic.dump(path, spec)
    aborted = semantic.run(path, runtime, tmp_path / "changed", "check", ScriptedModel())
    assert aborted["observations"][0]["status"] == "incomplete"
