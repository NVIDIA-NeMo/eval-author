# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills/eval-author-rubric/scripts/measure.py"
PREFIX = "provenance."


def package(root):
    sources = {
        "task.toml": 'schema_version = "1.3"\n[environment]\n[verifier]\n',
        "instruction.md": "Read /workspace/input.json and write /workspace/result.json.\n",
        "environment/Dockerfile": "FROM python:3.13\nWORKDIR /workspace\nCOPY input.json /workspace/input.json\n",
        "environment/input.json": '{"value": 17}\n',
        "tests/test.sh": "#!/bin/bash\nset -euo pipefail\nexec python /tests/check.py\n",
        "tests/check.py": "import json\nfrom helper import expected\nprint(json.dumps(expected))\n",
        "tests/helper.py": 'expected = {"value": 17}\n',
    }
    records = []
    for path, content in sources.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        records.append(
            {
                "id": path,
                "path": path,
                "role": "task" if path == "task.toml" else "dependency",
                "digest": digest,
                "revision": None,
                "access": "available",
                "selection_origin": "explicit example",
                "source": "task.toml",
                "evidence": [{"path": path, "digest": digest}],
            }
        )
    return {"artifacts": records, "example_assessment": {"task_config_id": "task.toml"}}


def run_cli(root, selection, *extra):
    path = root / "selection.json"
    path.write_text(json.dumps(selection))
    result = subprocess.run(
        [sys.executable, str(CLI), "--root", str(root), "--selection", str(path), *extra],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in (0, 1, 2), result.stderr
    return result, json.loads(result.stdout)


def add_source(root: Path, selection: dict, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    selection["artifacts"].append(
        {
            **selection["artifacts"][0],
            "id": path,
            "path": path,
            "role": "dependency",
            "digest": digest,
            "evidence": [{"path": path, "digest": digest}],
        }
    )


def test_cli_nonisolated_stdlib_shadow_is_inventoried_without_execution(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/test.sh", "exec python /tests/check.py\n")
    refresh(tmp_path, selection, "tests/check.py", "import json\n")
    sentinel = tmp_path / "shadow-executed"
    add_source(
        tmp_path,
        selection,
        "tests/json.py",
        f'from pathlib import Path\nPath({str(sentinel)!r}).write_text("executed")\n',
    )
    result, report = run_cli(tmp_path, selection)
    assert result.returncode == 1
    measured = report["measurements"]["example_assessment"]
    assert "tests/json.py" in measured["grading_dependencies"]["required"]
    assert report["items"][PREFIX + "grading-path"]["score"] == 1
    assert measured["route_gaps"]
    assert not sentinel.exists()
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 2


@pytest.mark.parametrize("flags", ["-I", "-I -S -B"])
def test_isolated_launch_does_not_resolve_sibling_helper(tmp_path, flags):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/test.sh", f"exec python {flags} /tests/check.py\n")
    _, report = run_cli(tmp_path, selection)
    measured = report["measurements"]["example_assessment"]
    assert report["items"][PREFIX + "grading-path"]["score"] == 1
    assert "tests/helper.py" not in measured["grading_dependencies"]["required"]
    assert any(gap.endswith("unresolved-import") for gap in measured["route_gaps"])


def test_absolute_isolated_python_keeps_explicit_sibling_source_and_runtime_gap(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/test.sh", "exec /usr/local/bin/python -I -S -B /tests/check.py\n")
    refresh(
        tmp_path,
        selection,
        "tests/check.py",
        "import sys\nfrom pathlib import Path\nsys.path.append(str(Path(__file__).resolve().parent))\nfrom helper import expected\n",
    )
    _, report = run_cli(tmp_path, selection)
    measured = report["measurements"]["example_assessment"]
    assert measured["scorer"] == "tests/check.py"
    assert "tests/helper.py" in measured["grading_dependencies"]["required"]
    assert measured["route_gaps"]
    assert report["items"][PREFIX + "grading-path"]["score"] == 1


def test_isolated_stdlib_import_does_not_inventory_local_shadow(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/test.sh", "exec python -I /tests/check.py\n")
    refresh(tmp_path, selection, "tests/check.py", "import json\n")
    add_source(tmp_path, selection, "tests/json.py", "raise RuntimeError('not executed')\n")
    _, report = run_cli(tmp_path, selection)
    measured = report["measurements"]["example_assessment"]
    assert "tests/json.py" not in measured["grading_dependencies"]["required"]
    assert measured["route_gaps"]
    assert report["items"][PREFIX + "grading-path"]["score"] == 1
    assert any(gap.endswith("unresolved-import") for gap in measured["route_gaps"])


@pytest.mark.parametrize("shadow", ["tests/json.py", "tests/json/__init__.py"])
def test_nonisolated_import_never_assumes_stdlib_from_partial_selection(tmp_path, shadow):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/test.sh", "exec python /tests/check.py\n")
    refresh(tmp_path, selection, "tests/check.py", "import json\n")
    target = tmp_path / shadow
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("raise RuntimeError('unselected private source')\n")
    _, report = run_cli(tmp_path, selection)
    measured = report["measurements"]["example_assessment"]
    assert report["items"][PREFIX + "grading-path"]["score"] == 1
    assert measured["route_gaps"]
    assert shadow not in measured["grading_dependencies"]["required"]


def test_runtime_origins_resolve_only_bound_source_imports(tmp_path):
    import importlib

    sys.path.insert(0, str(CLI.parent))
    importlib.import_module("skills.eval-author-rubric.scripts.measure")
    module = importlib.import_module("rubric_example_assessment")
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/check.py", "import json\n")
    sources = module.Sources(tmp_path, selection["artifacts"])
    launch = module.PythonLaunch("tests/check.py", True, (), "/usr/local/bin/python", ("-I", "-S", "-B"))
    gaps = {"tests/check.py:1:unresolved-import"}
    runtime = {
        "scope": "eval-author.refund-runtime-observation.v1",
        "observations": {
            "root": {
                "executable": "/usr/local/bin/python",
                "origins": {
                    "json": {
                        "file": "/usr/local/lib/python3.12/json/__init__.py",
                        "origin": "/usr/local/lib/python3.12/json/__init__.py",
                    }
                },
                "files": {"/usr/local/lib/python3.12/json/__init__.py": {"sha256": "a" * 64, "readable": True}},
            }
        },
    }
    module.resolve_runtime_imports(sources, launch, {"tests/check.py"}, gaps, runtime)
    assert gaps == set()
    gaps.add("tests/check.py:1:unresolved-import")
    runtime["observations"]["root"]["origins"].clear()
    module.resolve_runtime_imports(sources, launch, {"tests/check.py"}, gaps, runtime)
    assert gaps


def test_nonisolated_package_shadow_is_retained_as_unresolved_route(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/test.sh", "exec python /tests/check.py\n")
    refresh(tmp_path, selection, "tests/check.py", "import json\n")
    add_source(tmp_path, selection, "tests/json/__init__.py", "import hidden_grader\n")
    _, report = run_cli(tmp_path, selection)
    measured = report["measurements"]["example_assessment"]
    assert "tests/json/__init__.py" in measured["grading_dependencies"]["required"]
    assert report["items"][PREFIX + "grading-path"]["score"] == 1


def test_isolated_import_path_mutation_cannot_claim_resolved_route(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/test.sh", "exec python -I /tests/check.py\n")
    refresh(tmp_path, selection, "tests/check.py", 'import sys\nsys.path = ["/tests"]\nimport json\n')
    add_source(tmp_path, selection, "tests/json.py", "print('shadow')\n")
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "grading-path"]["score"] == 1
    assert report["measurements"]["example_assessment"]["route_gaps"]


def test_nested_imports_use_launcher_directory_not_importing_package(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/check.py", "import nested.worker\n")
    add_source(tmp_path, selection, "tests/nested/__init__.py", "")
    add_source(tmp_path, selection, "tests/nested/worker.py", "import helper\n")
    add_source(tmp_path, selection, "tests/nested/helper.py", "raise RuntimeError('wrong helper')\n")
    _, report = run_cli(tmp_path, selection)
    required = report["measurements"]["example_assessment"]["grading_dependencies"]["required"]
    assert "tests/helper.py" in required
    assert "tests/nested/__init__.py" in required
    assert "tests/nested/helper.py" not in required
    assert report["items"][PREFIX + "grading-path"]["score"] == 1


def test_cli_inspects_sources_without_executing_task(tmp_path):
    selection = package(tmp_path)
    result, report = run_cli(tmp_path, selection)
    assert result.returncode != 2, report
    scores = {
        name: report["items"][PREFIX + name]["score"]
        for name in ("task-artifact-access", "grading-path", "grading-dependency-access")
    }
    assert scores == {"task-artifact-access": 2, "grading-path": 1, "grading-dependency-access": 1}
    measured = report["measurements"]["example_assessment"]
    assert measured["scorer"] == "tests/check.py"
    assert "tests/helper.py" in measured["grading_dependencies"]["required"]
    assert measured["native_execution"] is False
    assert measured["human_review"] is False
    assert all(row["score"] is None for key, row in report["items"].items() if key not in {PREFIX + n for n in scores})
    for name in scores:
        row = report["items"][PREFIX + name]
        assert row["assessment_state"] == "ASSESSED"
        assert row["evidence_refs"]
        assert all(
            check["outcome"] == "NOT_RUN" for check in row["check_results"] if check["method"] != "deterministic"
        )


def refresh(root, selection, path, content):
    (root / path).write_text(content)
    digest = hashlib.sha256((root / path).read_bytes()).hexdigest()
    for record in selection["artifacts"]:
        if record["path"] == path:
            record["digest"] = digest
            record["evidence"] = [{"path": path, "digest": digest}]


@pytest.mark.parametrize("path", ["task.toml", "instruction.md", "tests/test.sh", "tests/check.py", "tests/helper.py"])
def test_stale_source_cannot_keep_a_score(tmp_path, path):
    selection = package(tmp_path)
    (tmp_path / path).write_text((tmp_path / path).read_text() + "\n# changed\n")
    result, report = run_cli(tmp_path, selection)
    assert result.returncode == 2
    assert report["code"] == "selection.invalid"


def test_missing_material_dependencies_lower_score_and_repair_restores_it(tmp_path):
    selection = package(tmp_path)
    path = tmp_path / "tests/helper.py"
    saved = path.read_bytes()
    path.unlink()
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "grading-dependency-access"]["score"] == 0
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 2
    path.write_bytes(saved)
    _, restored = run_cli(tmp_path, selection)
    assert restored["items"][PREFIX + "grading-dependency-access"]["score"] == 1


def test_unknown_dispatch_keeps_identified_wrapper_but_lowers_path_score(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/check.py", 'import os\nos.system(os.environ["SCORER"])\n')
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "grading-path"]["score"] == 1


@pytest.mark.parametrize("path", ["task.toml", "tests/test.sh", "tests/check.py"])
def test_denied_source_and_available_alias_cannot_promote_path(tmp_path, path):
    selection = package(tmp_path)
    original = next(record for record in selection["artifacts"] if record["path"] == path)
    selection["artifacts"].append({**original, "id": "denied-alias", "access": "denied"})
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "grading-path"]["score"] is None
    assert report["items"][PREFIX + "grading-dependency-access"]["score"] is None


def test_missing_inventory_member_is_not_a_confirmed_missing_file(tmp_path):
    selection = package(tmp_path)
    selection["artifacts"] = [row for row in selection["artifacts"] if row["path"] != "environment/input.json"]
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 1
    assert report["measurements"]["example_assessment"]["task_artifacts"]["counts"]["missing"] == 0


def test_full_directory_scope_confirms_missing_copy_input_and_preserves_neighbors(tmp_path):
    selection = package(tmp_path)
    selection["example_assessment"]["reference_scope"] = "task_directory"
    selection["artifacts"] = [row for row in selection["artifacts"] if row["path"] != "environment/input.json"]
    (tmp_path / "environment/input.json").unlink()
    result, report = run_cli(tmp_path, selection)
    assert result.returncode == 1, report
    measured = report["measurements"]["example_assessment"]
    missing = next(row for row in measured["task_artifacts"]["observations"] if row["path"] == "environment/input.json")
    assert missing["access"] == "missing"
    assert missing["digest"] is None
    assert measured["task_artifacts"]["counts"]["missing"] == 1
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 0
    assert report["items"][PREFIX + "grading-dependency-access"]["score"] == 1
    retained = tmp_path / "retained.json"
    retained.write_text(json.dumps(report))
    assert run_cli(tmp_path, selection, "--verify-existing", str(retained))[0].returncode == 1
    add_source(tmp_path, selection, "environment/input.json", '{"value": 17}\n')
    _, restored = run_cli(tmp_path, selection)
    assert restored["items"][PREFIX + "task-artifact-access"]["score"] == 2
    assert run_cli(tmp_path, selection, "--verify-existing", str(retained))[0].returncode == 2


@pytest.mark.parametrize("scope", [None, "task_directory"])
@pytest.mark.parametrize("state", ["absent", "present", "symlink"])
def test_reference_scope_does_not_promote_unselected_or_unsafe_bytes(tmp_path, scope, state):
    selection = package(tmp_path)
    if scope:
        selection["example_assessment"]["reference_scope"] = scope
    selection["artifacts"] = [row for row in selection["artifacts"] if row["path"] != "environment/input.json"]
    target = tmp_path / "environment/input.json"
    if state != "present":
        target.unlink()
    if state == "symlink":
        private = tmp_path / "private.json"
        private.write_text("PRIVATE_UNSELECTED_SENTINEL")
        target.symlink_to(private)
    result, report = run_cli(tmp_path, selection)
    assert result.returncode == 1
    observation = next(
        row
        for row in report["measurements"]["example_assessment"]["task_artifacts"]["observations"]
        if row["path"] == "environment/input.json"
    )
    assert observation["access"] == ("missing" if scope and state == "absent" else "unresolved")
    assert observation["digest"] is None
    assert "PRIVATE_UNSELECTED_SENTINEL" not in json.dumps(report)


def test_full_directory_scope_does_not_override_denied_alias(tmp_path):
    selection = package(tmp_path)
    selection["example_assessment"]["reference_scope"] = "task_directory"
    record = next(row for row in selection["artifacts"] if row["path"] == "environment/input.json")
    selection["artifacts"].append({**record, "id": "denied-input", "access": "denied"})
    _, report = run_cli(tmp_path, selection)
    observed = report["measurements"]["example_assessment"]["task_artifacts"]
    assert observed["counts"]["missing"] == 0
    assert observed["counts"]["unresolved"] == 1
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 1


@pytest.mark.parametrize("scope", [True, "complete", {}, ["task_directory"]])
def test_invalid_reference_scope_fails_closed(tmp_path, scope):
    selection = package(tmp_path)
    selection["example_assessment"]["reference_scope"] = scope
    result, report = run_cli(tmp_path, selection)
    assert result.returncode == 2
    assert report["code"] == "selection.invalid"


def test_materialized_missing_contract_through_task_table_cli(tmp_path):
    scripts = CLI.parent
    materialized = subprocess.run(
        [
            sys.executable,
            str(scripts / "example_fixtures.py"),
            "materialize",
            "--mutation",
            "missing-contract",
            "--output",
            str(tmp_path / "case"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert materialized.returncode == 0, materialized.stderr
    root = tmp_path / "case/assessment"
    output = tmp_path / "review"
    result = subprocess.run(
        [
            sys.executable,
            str(scripts / "rubric_table.py"),
            "--root",
            str(root),
            "--task",
            "task/task.toml",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stderr
    report = json.loads((output / "assessment.json").read_text())["report"]
    observed = report["measurements"]["example_assessment"]["task_artifacts"]
    assert (
        next(row for row in observed["observations"] if row["path"] == "task/environment/contract.json")["access"]
        == "missing"
    )
    assert observed["counts"]["missing"] == 1
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 0
    replay = subprocess.run(
        [sys.executable, str(scripts / "rubric_table.py"), "--root", str(root), "--verify-existing", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert replay.returncode == 1, replay.stderr


def test_actual_missing_input_prevents_inspection(tmp_path):
    selection = package(tmp_path)
    (tmp_path / "environment/input.json").unlink()
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 0


@pytest.mark.parametrize("assertion", ["score", "human_review", "independent_review", "complete"])
def test_assertions_are_not_an_assessment_input(tmp_path, assertion):
    selection = package(tmp_path)
    selection["example_assessment"][assertion] = True
    result, report = run_cli(tmp_path, selection)
    assert result.returncode == 2
    assert report["code"] == "selection.invalid"


@pytest.mark.parametrize(
    "path,content",
    [
        ("task.toml", "[broken"),
        ("task.toml", 'schema_version = "invented"\n[environment]\n[verifier]\n'),
        ("tests/check.py", "def broken(:"),
        ("tests/test.sh", "exec python '/tests/broken.py"),
    ],
)
def test_malformed_source_fails_closed(tmp_path, path, content):
    selection = package(tmp_path)
    refresh(tmp_path, selection, path, content)
    result, report = run_cli(tmp_path, selection)
    assert result.returncode == 2
    assert report["code"] == "selection.invalid"


def test_cli_retained_verification_rejects_tampered_score(tmp_path):
    selection = package(tmp_path)
    _, report = run_cli(tmp_path, selection)
    retained = tmp_path / "report.json"
    retained.write_text(json.dumps(report))
    result, _ = run_cli(tmp_path, selection, "--verify-existing", str(retained))
    assert result.returncode != 2
    report["items"][PREFIX + "grading-dependency-access"]["score"] = 3
    retained.write_text(json.dumps(report))
    result, _ = run_cli(tmp_path, selection, "--verify-existing", str(retained))
    assert result.returncode == 2


def test_public_input_reference_outside_copy_inventory_is_a_gap(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "instruction.md", "Read /workspace/other.json for the required inputs.\n")
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 1


def test_unknown_configuration_does_not_keep_complete_path(tmp_path):
    selection = package(tmp_path)
    refresh(
        tmp_path,
        selection,
        "task.toml",
        'schema_version="1.3"\n[environment]\n[verifier]\nentrypoint="/other/check.py"\n',
    )
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "grading-path"]["score"] == 1


def test_aliased_dynamic_dispatch_is_not_a_complete_route(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/check.py", 'from os import system as choose\nchoose("next-scorer")\n')
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "grading-path"]["score"] == 1


def test_unselected_private_file_never_becomes_evidence(tmp_path):
    selection = package(tmp_path)
    (tmp_path / "tests/private.json").write_text('{"claim":"irrelevant"}')
    refresh(
        tmp_path, selection, "tests/check.py", 'from pathlib import Path\nPath("/tests/private.json").read_text()\n'
    )
    _, report = run_cli(tmp_path, selection)
    measured = report["measurements"]["example_assessment"]["grading_dependencies"]
    private = next(row for row in measured["observations"] if row["path"] == "tests/private.json")
    assert private["digest"] is None
    assert private["access"] == "unresolved"


def test_missing_secondary_evidence_does_not_prove_missing_task(tmp_path):
    selection = package(tmp_path)
    record = next(row for row in selection["artifacts"] if row["path"] == "instruction.md")
    record["evidence"] = [{"path": "absent-review.json", "digest": "a" * 64}]
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 1


@pytest.mark.parametrize("field", ["route", "rubric_scope"])
def test_competing_asserted_selection_cannot_replace_inspection(tmp_path, field):
    selection = package(tmp_path)
    selection[field] = {}
    result, _ = run_cli(tmp_path, selection)
    assert result.returncode == 2


def test_symlink_source_fails_closed(tmp_path):
    selection = package(tmp_path)
    path = tmp_path / "tests/check.py"
    path.rename(tmp_path / "actual.py")
    path.symlink_to(tmp_path / "actual.py")
    result, _ = run_cli(tmp_path, selection)
    assert result.returncode == 2


def test_task_source_is_not_executed(tmp_path):
    selection = package(tmp_path)
    sentinel = tmp_path / "must-not-exist"
    refresh(
        tmp_path,
        selection,
        "tests/check.py",
        f'from pathlib import Path\nPath({str(sentinel)!r}).write_text("executed")\n',
    )
    run_cli(tmp_path, selection)
    assert not sentinel.exists()


def test_copy_destination_is_not_matched_by_basename(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "environment/Dockerfile", "FROM python:3.13\nCOPY input.json /hidden/input.json\n")
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 1


def test_transitive_import_without_selection_is_an_explicit_route_gap(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/helper.py", "import arbitrary_remote_grader\nexpected = 17\n")
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "grading-path"]["score"] == 1


def test_cli_argument_input_is_in_dependency_inventory(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/test.sh", "exec python /tests/check.py --input /tests/expected.json\n")
    _, report = run_cli(tmp_path, selection)
    assert "tests/expected.json" in report["measurements"]["example_assessment"]["grading_dependencies"]["required"]


def test_missing_task_manifest_is_unassessed_not_partial_inspection(tmp_path):
    selection = package(tmp_path)
    (tmp_path / "task.toml").unlink()
    _, report = run_cli(tmp_path, selection)
    assert all(
        report["items"][PREFIX + name]["score"] is None
        for name in ("task-artifact-access", "grading-path", "grading-dependency-access")
    )


def test_source_inventory_retains_origins_and_canonical_counts(tmp_path):
    selection = package(tmp_path)
    _, report = run_cli(tmp_path, selection)
    measured = report["measurements"]["example_assessment"]
    assert {"source": "tests/check.py", "target": "tests/helper.py", "kind": "python-import", "line": 2} in measured[
        "references"
    ]
    assert report["items"][PREFIX + "task-artifact-access"]["measurements"]


def test_missing_asserted_digest_cannot_promote_scoring(tmp_path):
    selection = package(tmp_path)
    next(row for row in selection["artifacts"] if row["path"] == "tests/check.py")["digest"] = None
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "grading-path"]["score"] is None


def test_environment_override_leaves_inventory_incomplete(tmp_path):
    selection = package(tmp_path)
    refresh(
        tmp_path, selection, "task.toml", 'schema_version="1.3"\n[environment]\nmounts=["/elsewhere"]\n[verifier]\n'
    )
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 1


def test_source_with_unresolved_shell_interpolation_has_no_path_score(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/test.sh", 'exec python "$SCORER"\n')
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "grading-path"]["score"] is None


def test_literal_reads_in_nested_scorer_use_harbor_tests_root(tmp_path):
    selection = package(tmp_path)
    path = tmp_path / "tests/nested/check.py"
    path.parent.mkdir()
    path.write_text('from pathlib import Path\nPath("/tests/expected.json").read_text()\n')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    record = next(row for row in selection["artifacts"] if row["path"] == "tests/check.py")
    selection["artifacts"].append(
        {
            **record,
            "id": "nested-check",
            "path": "tests/nested/check.py",
            "digest": digest,
            "evidence": [{"path": "tests/nested/check.py", "digest": digest}],
        }
    )
    refresh(tmp_path, selection, "tests/test.sh", "exec python /tests/nested/check.py\n")
    _, report = run_cli(tmp_path, selection)
    deps = report["measurements"]["example_assessment"]["grading_dependencies"]["required"]
    assert "tests/expected.json" in deps
    assert "tests/nested/expected.json" not in deps


def test_existing_recorded_claims_cannot_make_independence_real(tmp_path):
    selection = package(tmp_path)
    refresh(
        tmp_path,
        selection,
        "tests/helper.py",
        'expected = {"score": 3, "complete": True, "human_review": True, "independent": True}\n',
    )
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "grading-dependency-access"]["score"] == 1
    assert all(row["score"] != 3 for row in report["items"].values())


def test_unknown_callable_cannot_claim_resolved_dispatch(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/check.py", 'chooser = getattr(__builtins__, "eval")\nchooser("1")\n')
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "grading-path"]["score"] == 1


def test_alias_order_does_not_change_access_or_scores(tmp_path):
    selection = package(tmp_path)
    original = next(record for record in selection["artifacts"] if record["path"] == "tests/check.py")
    selection["artifacts"].append({**original, "id": "denied-alias", "access": "denied"})
    _, first = run_cli(tmp_path, selection)
    selection["artifacts"].reverse()
    _, second = run_cli(tmp_path, selection)
    assert first["measurements"]["example_assessment"] == second["measurements"]["example_assessment"]


def test_indirect_callable_is_outside_complete_route_subset(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/check.py", 'import os\nchooser = os.system\nchooser("next-scorer")\n')
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "grading-path"]["score"] == 1


def test_task_configuration_sources_partial_identity_without_native_claims(tmp_path):
    selection = package(tmp_path)
    refresh(
        tmp_path,
        selection,
        "task.toml",
        'schema_version="1.3"\n[environment]\ncpus=2\nmemory_mb=2048\n'
        'network_mode="none"\n[verifier]\ntimeout_sec=60\nuser="root"\n'
        "[agent]\ntimeout_sec=120\n",
    )
    result, report = run_cli(tmp_path, selection)
    assert result.returncode != 2
    row = report["items"][PREFIX + "declared-configuration"]
    assert row["score"] == 1
    fields = report["measurements"]["example_assessment"]["configuration"]["fields"]
    assert fields["/run/verifier_timeout_sec"]["declared"]["state"] == "sourced"
    assert fields["/agent/model"]["declared"]["state"] == "omitted"
    assert report["items"][PREFIX + "observed-configuration"]["score"] is None
    assert report["items"][PREFIX + "runtime-route"]["score"] is None
    assert row["check_results"][0]["outcome"] == "PARTIAL"
    assert "60" not in json.dumps(fields)  # Configuration values remain private.


def add_review(root, selection, report):
    definitions = ROOT / "skills/eval-author-rubric/references"
    document = {
        "schema": "rubric-source-review-v1",
        "task_config_id": "task.toml",
        "definition_digests": {
            name: hashlib.sha256((definitions / name).read_bytes()).hexdigest()
            for name in (
                "artifact-configuration-provenance.json",
                "task-contract-authority.json",
                "artifact-configuration-provenance.md",
                "task-contract-authority.md",
                "anti-cheat.md",
            )
        },
        "sources": sorted(
            [{"id": row["id"], "digest": row["digest"]} for row in selection["artifacts"]], key=lambda row: row["id"]
        ),
        "reviewer": {"kind": "human", "identity": "Synthetic test reviewer", "session_id": "test-only"},
        "decisions": [
            {
                "check_id": "task-contract.clear-contract.human-review",
                "status": "supported",
                "rationale": "Synthetic intake control, not a real owner approval.",
                "citations": [
                    {
                        "artifact_id": "instruction.md",
                        "start_line": 1,
                        "end_line": 1,
                        "quote": (root / "instruction.md").read_text(),
                    }
                ],
            }
        ],
    }
    path = "review.json"
    (root / path).write_text(json.dumps(document))
    digest = hashlib.sha256((root / path).read_bytes()).hexdigest()
    selection["artifacts"].append(
        {
            **selection["artifacts"][0],
            "id": path,
            "path": path,
            "role": "review",
            "digest": digest,
            "evidence": [{"path": path, "digest": digest}],
        }
    )
    selection["example_assessment"]["review_ids"] = [path]
    return document


def test_source_review_intake_keeps_decisions_separate_from_measured_checks(tmp_path):
    selection = package(tmp_path)
    _, before = run_cli(tmp_path, selection)
    add_review(tmp_path, selection, before)
    without_review = {**selection, "example_assessment": {"task_config_id": "task.toml"}}
    _, before = run_cli(tmp_path, without_review)
    result, after = run_cli(tmp_path, selection)
    assert result.returncode != 2
    reviews = after["measurements"]["example_assessment"]["source_reviews"]
    assert reviews["records"][0]["binding_status"] == "current"
    assert reviews["records"][0]["authority_authenticated"] is False
    assert reviews["records"][0]["independence_established"] is False
    assert reviews["records"][0]["decisions"][0]["status"] == "supported"
    for ident, original in before["items"].items():
        assert after["items"][ident]["score"] == original["score"]
        assert after["items"][ident]["check_results"] == original["check_results"]
    for name in ("task-artifact-access", "grading-path", "grading-dependency-access"):
        assert after["items"][PREFIX + name] == before["items"][PREFIX + name]
    row = after["items"]["task-contract.clear-contract"]
    assert row["recorded_source_reviews"]
    assert row["score"] is None


@pytest.mark.parametrize(
    "mutation", ["quote", "span", "sources", "definition", "check", "score", "authority", "duplicate"]
)
def test_review_tampering_cannot_be_resealed_into_acceptance(tmp_path, mutation):
    selection = package(tmp_path)
    document = add_review(tmp_path, selection, None)
    decision = document["decisions"][0]
    if mutation == "quote":
        decision["citations"][0]["quote"] = "Invented authority"
    elif mutation == "span":
        decision["citations"][0]["start_line"] = True
    elif mutation == "sources":
        document["sources"].pop()
    elif mutation == "definition":
        document["definition_digests"]["task-contract-authority.json"] = "a" * 64
    elif mutation == "check":
        decision["check_id"] = "task-contract.scoring-authority.probe"
    elif mutation == "score":
        decision["score"] = 3
    elif mutation == "authority":
        document["reviewer"]["authenticated"] = True
    else:
        document["decisions"].append(decision.copy())
    refresh(tmp_path, selection, "review.json", json.dumps(document))
    result, report = run_cli(tmp_path, selection)
    assert result.returncode == 2
    assert report["code"] == "selection.invalid"


def test_review_is_invalidated_after_source_refresh_without_rewriting_history(tmp_path):
    selection = package(tmp_path)
    add_review(tmp_path, selection, None)
    review_before = (tmp_path / "review.json").read_bytes()
    refresh(tmp_path, selection, "instruction.md", "Read /workspace/input.json. New obligation.\n")
    result, _ = run_cli(tmp_path, selection)
    assert result.returncode == 2
    assert (tmp_path / "review.json").read_bytes() == review_before


def test_missing_review_retains_measurement_and_unknown_authority(tmp_path):
    selection = package(tmp_path)
    add_review(tmp_path, selection, None)
    (tmp_path / "review.json").unlink()
    result, report = run_cli(tmp_path, selection)
    assert result.returncode != 2
    assert (
        report["measurements"]["example_assessment"]["source_reviews"]["records"][0]["binding_status"] == "unavailable"
    )
    assert report["items"]["task-contract.clear-contract"]["score"] is None
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 2


def test_review_supplemental_scoring_rules_are_bound(tmp_path):
    selection = package(tmp_path)
    document = add_review(tmp_path, selection, None)
    document["definition_digests"].pop("task-contract-authority.md", None)
    refresh(tmp_path, selection, "review.json", json.dumps(document))
    result, _ = run_cli(tmp_path, selection)
    assert result.returncode == 2


def test_readonly_assessment_accounts_for_every_canonical_check(tmp_path):
    selection = package(tmp_path)
    _, report = run_cli(tmp_path, selection)
    assert len(report["items"]) == 18
    assert sum(len(row["check_results"]) for row in report["items"].values()) == 59
    assert all(row["subject_id"] == "task.toml" for row in report["items"].values())
    gates = report["measurements"]["example_assessment"]["qualification_gates"]
    assert set(gates) == set(report["items"])
    assert all(gate["target_score"] == 3 for gate in gates.values())
    assert gates["task-contract.clear-contract"]["human_adjudication_required"] is True
    assert gates["provenance.evidence-revision-consistency"]["independence_required"] is False
    assert gates["task-contract.scoring-authority"]["native_required"] is False
    assert gates["AC-01"]["native_required"] is True


@pytest.mark.parametrize("method", ["AC-01:Review", "AC-04:Review"])
def test_anti_cheat_review_is_recorded_without_changing_native_floor(tmp_path, method):
    selection = package(tmp_path)
    document = add_review(tmp_path, selection, None)
    document["decisions"][0]["check_id"] = method
    document["reviewer"]["kind"] = "agent"
    refresh(tmp_path, selection, "review.json", json.dumps(document))
    result, report = run_cli(tmp_path, selection)
    assert result.returncode != 2
    row = report["items"][method.split(":")[0]]
    assert row["recorded_source_reviews"][0]["decision"]["check_id"] == method
    assert row["score"] is None
    assert all(check["outcome"] == "NOT_RUN" for check in row["check_results"])


@pytest.mark.parametrize(
    "field,value", [("cpus", "true"), ("cpus", '"two"'), ("memory_mb", "-1"), ("network_mode", "[]")]
)
def test_malformed_resource_setting_cannot_support_configuration_score(tmp_path, field, value):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "task.toml", f'schema_version="1.3"\n[environment]\n{field}={value}\n[verifier]\n')
    result, report = run_cli(tmp_path, selection)
    assert result.returncode == 2
    assert report["code"] == "selection.invalid"


def test_config_missing_observations_stay_unknown_and_tamper_fails_replay(tmp_path):
    selection = package(tmp_path)
    refresh(
        tmp_path, selection, "task.toml", 'schema_version="1.3"\n[environment]\ncpus=2\n[verifier]\ntimeout_sec=60\n'
    )
    _, report = run_cli(tmp_path, selection)
    retained = tmp_path / "retained.json"
    retained.write_text(json.dumps(report))
    result, _ = run_cli(tmp_path, selection, "--verify-existing", str(retained))
    assert result.returncode != 2
    configuration = report["measurements"]["example_assessment"]["configuration"]
    assert configuration["counts"]["observed_sourced"] == 0
    configuration["counts"]["declared_sourced"] += 1
    retained.write_text(json.dumps(report))
    result, _ = run_cli(tmp_path, selection, "--verify-existing", str(retained))
    assert result.returncode == 2


def test_configuration_source_with_path_style_id_uses_shared_id_policy(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "task.toml", 'schema_version="1.3"\n[environment]\ncpus=2\n[verifier]\n')
    selection["artifacts"][0]["id"] = "task/task.toml"
    selection["example_assessment"]["task_config_id"] = "task/task.toml"
    result, report = run_cli(tmp_path, selection)
    assert result.returncode != 2
    assert report["items"][PREFIX + "declared-configuration"]["score"] == 1


@pytest.mark.parametrize("mutation", ["missing_row", "review_decision", "review_authority"])
def test_cli_replay_rejects_missing_rows_or_altered_review(tmp_path, mutation):
    selection = package(tmp_path)
    add_review(tmp_path, selection, None)
    _, report = run_cli(tmp_path, selection)
    retained = tmp_path / "retained.json"
    retained.write_text(json.dumps(report))
    assert run_cli(tmp_path, selection, "--verify-existing", str(retained))[0].returncode != 2
    if mutation == "missing_row":
        del report["items"]["AC-04"]
    elif mutation == "review_decision":
        report["measurements"]["example_assessment"]["source_reviews"]["records"][0]["decisions"][0]["status"] = (
            "contradicted"
        )
    else:
        report["items"]["task-contract.clear-contract"]["recorded_source_reviews"][0]["authority_authenticated"] = True
    retained.write_text(json.dumps(report))
    assert run_cli(tmp_path, selection, "--verify-existing", str(retained))[0].returncode == 2


def test_root_replacement_during_example_assessment_fails_closed(tmp_path, monkeypatch):
    import importlib.util
    import shutil

    selected = tmp_path / "selected"
    selected.mkdir()
    selection = package(selected)
    monkeypatch.syspath_prepend(str(CLI.parent))
    monkeypatch.syspath_prepend(str(ROOT / "skills/eval-author/scripts"))
    spec = importlib.util.spec_from_file_location("tested_example_measure", CLI)
    assert spec is not None and spec.loader is not None
    measure_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(measure_module)
    import rubric_example_assessment

    original = rubric_example_assessment.project
    calls = 0

    def replace_root(root, request):
        nonlocal calls
        calls += 1
        if calls == 2:
            root.rename(tmp_path / "original")
            shutil.copytree(tmp_path / "original", root)
        return original(root, request)

    monkeypatch.setattr(rubric_example_assessment, "project", replace_root)
    with pytest.raises(ValueError, match="selected evidence changed"):
        measure_module.measure(selected, selection)


def test_review_attachment_survives_owning_contract_source_projection(tmp_path):
    from test_rubric_contract_controls import packet

    selection = packet(tmp_path)
    selection["example_assessment"] = {"task_config_id": "task_config_id"}
    document = add_review(tmp_path, selection, None)
    document["task_config_id"] = "task_config_id"
    document["decisions"][0]["citations"][0]["artifact_id"] = "instruction_id"
    document["decisions"][0]["citations"][0]["end_line"] = len((tmp_path / "instruction.md").read_text().splitlines())
    refresh(tmp_path, selection, "review.json", json.dumps(document))
    result, report = run_cli(tmp_path, selection)
    assert result.returncode != 2
    row = report["items"]["task-contract.clear-contract"]
    assert row["recorded_source_reviews"]
    assert row["score"] is None
    assert report["items"]["task-contract.discoverable-requirements"]["score"] == 0


def test_missing_dependency_has_evidence_reference_and_failure_measurement(tmp_path):
    selection = package(tmp_path)
    (tmp_path / "tests/helper.py").unlink()
    _, report = run_cli(tmp_path, selection)
    row = report["items"][PREFIX + "grading-dependency-access"]
    assert any(ref["path"] == "tests/helper.py" and ref["access"] == "missing" for ref in row["evidence_refs"])
    assert report["measurements"]["example_assessment"]["status"] == "fail"


def refund_package(root):
    base = ROOT / "skills/eval-author-rubric/examples/refund/base"
    selection = {
        "artifacts": [],
        "example_assessment": {"task_config_id": "task.toml", "reference_scope": "task_directory"},
    }
    for path in sorted(base.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            relative = path.relative_to(base).as_posix()
            raw = path.read_bytes()
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            selection["artifacts"].append(
                {
                    "id": relative,
                    "path": relative,
                    "role": "task" if relative == "task.toml" else "dependency",
                    "digest": digest,
                    "revision": None,
                    "access": "available",
                    "selection_origin": "explicit example",
                    "source": "task.toml",
                    "evidence": [{"path": relative, "digest": digest}],
                }
            )
    return selection


def configuration_companion(root, selection, agent="oracle"):
    selected = root / "input-selection.json"
    selected.write_text(json.dumps(selection))
    result = subprocess.run(
        [
            sys.executable,
            str(CLI.parent / "rubric_example_assessment.py"),
            "--root",
            str(root),
            "--selection",
            str(selected),
            "--agent",
            agent,
            "--output",
            str(root / "configuration"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return json.loads((root / "configuration/selection.json").read_text())


@pytest.mark.parametrize("agent", ["oracle", "nop"])
def test_source_backed_configuration_companion_closes_declared_anchor(tmp_path, agent):
    selection = configuration_companion(tmp_path, refund_package(tmp_path), agent)
    result, report = run_cli(tmp_path, selection)
    assert result.returncode == 1, report
    row = report["items"][PREFIX + "declared-configuration"]
    assert row["score"] == 2
    measured = report["measurements"]["example_assessment"]
    config = measured["configuration"]["companion"]
    assert config["agent"]["name"] == agent
    assert config["agent"]["kind"] == "scripted"
    assert config["complete"] is True
    assert config["provider_hidden"] == []
    assert config["fields"]["/task/environment/storage_mb"]["state"] == "sourced_default"
    assert config["fields"]["/task/environment/env"]["state"] == "sourced_default"
    assert config["fields"]["/agent/model"]["state"] == "not_applicable"
    assert all(field.get("provenance") for field in config["fields"].values())
    assert measured["native_execution"] is False
    assert report["items"][PREFIX + "observed-configuration"]["score"] is None
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 2
    assert row["check_results"][0]["outcome"] == "PASS"
    assert row["check_results"][-1]["outcome"] == "NOT_RUN"
    retained = tmp_path / "retained.json"
    retained.write_text(json.dumps(report))
    assert run_cli(tmp_path, selection, "--verify-existing", str(retained))[0].returncode == 1


def test_configuration_future_model_keeps_omissions_distinct(tmp_path):
    selection = configuration_companion(tmp_path, refund_package(tmp_path), "model")
    result, report = run_cli(tmp_path, selection)
    assert result.returncode == 1
    config = report["measurements"]["example_assessment"]["configuration"]["companion"]
    assert config["agent"] == {"name": "model", "kind": "model"}
    assert config["fields"]["/agent/model"]["state"] == "omitted"
    assert config["provider_hidden"] == []
    assert report["items"][PREFIX + "declared-configuration"]["score"] == 1


@pytest.mark.parametrize("mutation", ["field", "source", "agent", "task", "complete", "inventory"])
def test_configuration_companion_resealed_tampering_is_rejected(tmp_path, mutation):
    selection = configuration_companion(tmp_path, refund_package(tmp_path))
    path = "configuration/declared-configuration.json"
    document = json.loads((tmp_path / path).read_text())
    if mutation == "field":
        document["manifest"]["fields"].pop("/task/environment/storage_mb")
    elif mutation == "source":
        source = document["runtime_sources"]["harbor/models/task/config.py"]
        refresh(
            tmp_path,
            selection,
            source,
            (tmp_path / source).read_text().replace("storage_mb: int | None = None", "storage_mb: int | None = 1024"),
        )
    elif mutation == "agent":
        document["agent"] = "model"
    elif mutation == "task":
        document["task_config_id"] = "instruction.md"
    elif mutation == "complete":
        document["complete"] = True
    else:
        document["runtime_sources"].pop("harbor/agents/oracle.py")
    refresh(tmp_path, selection, path, json.dumps(document))
    result, report = run_cli(tmp_path, selection)
    assert result.returncode == 2
    assert report["code"] == "selection.invalid"


@pytest.mark.parametrize("mutation", ["source", "extra", "env", "image", "unknown"])
def test_configuration_companion_does_not_qualify_unsupported_task_settings(tmp_path, mutation):
    selection = refund_package(tmp_path)
    if mutation == "source":
        refresh(
            tmp_path,
            selection,
            "solution/reference.py",
            (tmp_path / "solution/reference.py").read_text() + "\nimport unseen_model\n",
        )
    elif mutation == "extra":
        add_source(tmp_path, selection, "settings.py", "UNSELECTED_OPTION = 1\n")
        selection["artifacts"][-1]["role"] = "configuration"
    else:
        content = (tmp_path / "task.toml").read_text()
        change = {
            "env": 'env={TOKEN="${TOKEN}"}',
            "image": 'docker_image="different:latest"',
            "unknown": "other_setting=true",
        }[mutation]
        refresh(tmp_path, selection, "task.toml", content.replace("[environment]", "[environment]\n" + change))
    selection = configuration_companion(tmp_path, selection)
    result, report = run_cli(tmp_path, selection)
    assert result.returncode == 1
    assert report["items"][PREFIX + "declared-configuration"]["score"] == 1


def test_configuration_complete_manifest_replaces_stale_omission_claims(tmp_path):
    selection = configuration_companion(tmp_path, refund_package(tmp_path))
    _, report = run_cli(tmp_path, selection)
    measured = report["measurements"]["example_assessment"]["configuration"]
    assert measured["fields"]["/agent/model"]["declared"]["state"] == "not_applicable"
    assert not any("not select" in limit for limit in measured["source_limits"])
    fields = measured["companion"]["fields"]
    assert fields["/task/verifier/resolved_environment_mode"]["value"] == "shared"
    assert fields["/task/environment/storage_mb"]["value"] is None
    assert fields["/task/environment/env"]["value"] == {}
    assert fields["/trial/environment/cpu_enforcement_policy"]["value"] == "auto"
    assert any(key.startswith("/source/") for key in fields)


@pytest.mark.parametrize("target", ["companion", "runtime", "solution"])
@pytest.mark.parametrize("state", ["missing", "denied", "symlink"])
def test_configuration_unavailable_sources_never_preserve_score_two(tmp_path, target, state):
    selection = configuration_companion(tmp_path, refund_package(tmp_path))
    path = {
        "companion": "configuration/declared-configuration.json",
        "runtime": "configuration/sources/harbor/models/task/config.py",
        "solution": "solution/reference.py",
    }[target]
    record = next(row for row in selection["artifacts"] if row["path"] == path)
    if state == "missing":
        (tmp_path / path).unlink()
    elif state == "denied":
        selection["artifacts"].append({**record, "id": "denied-source", "access": "denied"})
    else:
        raw = (tmp_path / path).read_bytes()
        (tmp_path / "private-source").write_bytes(raw)
        (tmp_path / path).unlink()
        (tmp_path / path).symlink_to(tmp_path / "private-source")
    result, report = run_cli(tmp_path, selection)
    assert result.returncode == 2 or report["items"][PREFIX + "declared-configuration"]["score"] == 1


def test_configuration_companion_captures_all_constructor_defaults_and_argv(tmp_path):
    selection = configuration_companion(tmp_path, refund_package(tmp_path))
    _, report = run_cli(tmp_path, selection)
    config = report["measurements"]["example_assessment"]["configuration"]["companion"]
    assert config["collector_argv"][-1] == "--delete"
    assert config["collector_argv"][config["collector_argv"].index("-a") + 1] == "oracle"
    assert any(
        field["provenance"].get("symbol") == "DockerEnvironment.__init__.keep_containers"
        for field in config["fields"].values()
    )
    assert any(
        field["provenance"].get("symbol") == "OracleAgent.__init__.model_name" for field in config["fields"].values()
    )
    assert any(field["provenance"].get("symbol") == "private_environment" for field in config["fields"].values())


@pytest.mark.parametrize("output_name", ["../escape", "configuration/../../escape"])
def test_configuration_companion_rejects_parent_traversal(tmp_path, output_name):
    selected = tmp_path / "selection.json"
    selected.write_text(json.dumps(refund_package(tmp_path)))
    result = subprocess.run(
        [
            sys.executable,
            str(CLI.parent / "rubric_example_assessment.py"),
            "--root",
            str(tmp_path),
            "--selection",
            str(selected),
            "--agent",
            "oracle",
            "--output",
            str(tmp_path / output_name),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert not (tmp_path.parent / "escape").exists()


def test_configuration_companion_table_replay_stays_within_reader_limit(tmp_path):
    selection = configuration_companion(tmp_path, refund_package(tmp_path))
    selected = tmp_path / "table-selection.json"
    selected.write_text(json.dumps(selection))
    table = CLI.parent / "rubric_table.py"
    output = tmp_path / "table"
    result = subprocess.run(
        [sys.executable, str(table), "--root", str(tmp_path), "--selection", str(selected), "--output", str(output)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stderr
    replay = subprocess.run(
        [sys.executable, str(table), "--root", str(tmp_path), "--verify-existing", str(output)],
        capture_output=True,
        text=True,
    )
    assert replay.returncode == 1, replay.stdout + replay.stderr


def test_refund_literal_build_closes_task_inputs_without_runtime_claim(tmp_path):
    selection = refund_package(tmp_path)
    _, report = run_cli(tmp_path, selection)
    observed = report["measurements"]["example_assessment"]
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 2
    assert observed["task_artifacts"]["unresolved_material"] == []
    build = observed["source_closure"]["build"]
    assert any(row["operation"] == "ENTRYPOINT" and row["line"] == 7 for row in build)
    assert any(row["operation"] == "RUN" and row["line"] == 2 for row in build)
    assert observed["native_execution"] is False
    assert observed["human_review"] is False


@pytest.mark.parametrize(
    "command",
    [
        "RUN chmod 444 /app/contract.json; curl https://invalid.example/input",
        "RUN chmod $MODE /app/contract.json",
        'ENTRYPOINT ["python", "-c", "import hidden"]',
        "COPY --from=other /private /app/contract.json",
    ],
)
def test_unsupported_build_cannot_reuse_literal_permission_closure(tmp_path, command):
    selection = refund_package(tmp_path)
    path = "environment/Dockerfile"
    refresh(tmp_path, selection, path, (tmp_path / path).read_text() + command + "\n")
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 1


@pytest.mark.parametrize(
    "source",
    [
        'from pathlib import Path\np = Path("/tests/a.json")\np = Path(input())\np.read_text()\n',
        'from pathlib import Path\ndef read(p):\n    return p.read_text()\nread(Path("/tests/a.json"))\nread(Path(input()))\n',
        'import json\ndef main(json):\n    return json.loads("{}")\nmain(input())\n',
    ],
)
def test_ambiguous_path_or_callable_binding_cannot_close_route(tmp_path, source):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/test.sh", "python -I /tests/check.py\n")
    refresh(tmp_path, selection, "tests/check.py", source)
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "grading-path"]["score"] == 1


def test_selected_missing_literal_read_cannot_leave_route_complete(tmp_path):
    selection = package(tmp_path)
    refresh(tmp_path, selection, "tests/test.sh", "python -I /tests/check.py\n")
    refresh(tmp_path, selection, "tests/check.py", 'from pathlib import Path\nPath("/tests/absent.json").read_text()\n')
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "grading-path"]["score"] == 1


def test_refund_selected_grader_branch_is_literal_not_contract_metadata(tmp_path):
    selection = refund_package(tmp_path)
    _, report = run_cli(tmp_path, selection)
    closure = report["measurements"]["example_assessment"]["source_closure"]
    call = next(row for row in closure["calls"] if row["callee"] == "evaluate")
    assert call["literal_arguments"]["grader"] == "reviewed"
    assert any(row["expression"] == "grader == 'baseline'" and row["selected"] is False for row in closure["branches"])


def test_dynamic_second_grader_call_invalidates_literal_binding(tmp_path):
    selection = refund_package(tmp_path)
    path = "tests/verify.py"
    refresh(
        tmp_path, selection, path, (tmp_path / path).read_text() + '\nevaluate({}, {}, grader=os.environ["GRADER"])\n'
    )
    _, report = run_cli(tmp_path, selection)
    measured = report["measurements"]["example_assessment"]
    assert any("conflicting-call-binding" in gap for gap in measured["route_gaps"])
    assert not any(row["expression"] == "grader == 'baseline'" for row in measured["source_closure"]["branches"])


def test_conflicting_grader_calls_keep_branch_selection_unresolved(tmp_path):
    selection = refund_package(tmp_path)
    path = "tests/verify.py"
    refresh(tmp_path, selection, path, (tmp_path / path).read_text() + '\nevaluate({}, {}, grader="baseline")\n')
    _, report = run_cli(tmp_path, selection)
    measured = report["measurements"]["example_assessment"]
    assert any("conflicting-call-binding" in gap for gap in measured["route_gaps"])
    assert not any(row["expression"] == "grader == 'baseline'" for row in measured["source_closure"]["branches"])


def test_refund_source_settings_cover_limits_and_callers(tmp_path):
    selection = refund_package(tmp_path)
    _, report = run_cli(tmp_path, selection)
    closure = report["measurements"]["example_assessment"]["source_closure"]
    assert any(row["callee"] == "evaluate" and row["caller"] == "main" for row in closure["calls"])
    settings = closure["settings"]
    assert any(row["source"] == "environment/server.py" and row["name"] == "settimeout" for row in settings)
    assert any(row["source"] == "environment/ledger.py" and "10000" in row["expression"] for row in settings)
    assert any(row["kind"] == "json-setting" and row["pointer"].startswith("/scoring/") for row in settings)


def test_transitive_service_input_cannot_hide_behind_copied_module(tmp_path):
    selection = refund_package(tmp_path)
    path = "environment/ledger.py"
    refresh(
        tmp_path,
        selection,
        path,
        (tmp_path / path).read_text() + '\nfrom pathlib import Path\nPath("/app/private.json").read_text()\n',
    )
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 1


def test_service_unselected_module_is_an_input_gap(tmp_path):
    selection = refund_package(tmp_path)
    path = "environment/server.py"
    refresh(tmp_path, selection, path, (tmp_path / path).read_text() + "\nimport unseen_input_provider\n")
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 1


def test_service_dynamic_import_remains_a_task_input_gap(tmp_path):
    selection = refund_package(tmp_path)
    path = "environment/server.py"
    refresh(tmp_path, selection, path, (tmp_path / path).read_text() + '\n__import__(os.environ["TASK_MODULE"])\n')
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 1


def test_service_new_input_is_not_hidden_by_literal_entrypoint(tmp_path):
    selection = refund_package(tmp_path)
    path = "environment/server.py"
    refresh(tmp_path, selection, path, (tmp_path / path).read_text() + '\nPath("/app/extra.json").read_text()\n')
    _, report = run_cli(tmp_path, selection)
    assert report["items"][PREFIX + "task-artifact-access"]["score"] == 1
    assert any(
        "/app/extra.json" in gap
        for gap in report["measurements"]["example_assessment"]["task_artifacts"]["unresolved_material"]
    )


def test_refund_record_paths_bind_to_service_producer(tmp_path):
    selection = refund_package(tmp_path)
    _, report = run_cli(tmp_path, selection)
    measured = report["measurements"]["example_assessment"]
    assert not any("runtime-file-origin" in gap for gap in measured["grading_dependencies"]["unresolved_material"])
    records = [row for row in measured["source_closure"]["paths"] if row["resolved"] == "/var/lib/refund/record.json"]
    assert any(row.get("producer") == "environment/ledger.py" for row in records)
    assert any(row.get("kind") == "generated-state" for row in records)


def test_refund_configuration_includes_phase_settings_and_source_defaults(tmp_path):
    selection = refund_package(tmp_path)
    _, report = run_cli(tmp_path, selection)
    measured = report["measurements"]["example_assessment"]
    fields = measured["configuration"]["fields"]
    for key in (
        "/tool_permissions/agent_user",
        "/environment/build_timeout_sec",
        "/tool_permissions/agent_network_mode",
        "/tool_permissions/verifier_network_mode",
    ):
        assert fields[key]["declared"]["state"] == "sourced"
    settings = measured["source_closure"]["settings"]
    assert any(
        row["source"] == "environment/server.py" and row["kind"] == "argument-default" and row["name"] == "--record"
        for row in settings
    )
    assert any(row["source"] == "tests/verify.py" and row["name"] == "GRADER" for row in settings)
    assert measured["configuration"]["source_limits"]
    assert measured["configuration"]["provider_hidden"] == []
    assert report["items"][PREFIX + "observed-configuration"]["score"] is None


def test_refund_source_calls_and_cli_paths_have_expression_evidence(tmp_path):
    selection = refund_package(tmp_path)
    _, report = run_cli(tmp_path, selection)
    observed = report["measurements"]["example_assessment"]
    gaps = observed["grading_dependencies"]["unresolved_material"]
    startup_line = next(
        index
        for index, line in enumerate((tmp_path / "tests/verify.py").read_text().splitlines(), 1)
        if line.startswith("sys.path.append(")
    )
    assert all(
        gap.startswith(f"tests/verify.py:{startup_line}:")
        for gap in gaps
        if "code-dispatch-review-required" in gap or "computed-file-reference" in gap
    )
    assert not any("completeness requires source review" in gap for gap in gaps)
    calls = observed["source_closure"]["calls"]
    assert any(row["callee"] == "evaluate" and row["binding"] == "refund_scorer.evaluate" for row in calls)
    paths = observed["source_closure"]["paths"]
    assert any(row["resolved"] == "/app/contract.json" and row["expression"] == "Path(path)" for row in paths)
    assert any(row["resolved"] == "/var/lib/refund/record.closed" for row in paths)
    assert all(
        gap.endswith("unresolved-import") or gap.startswith(f"tests/verify.py:{startup_line}:")
        for gap in observed["route_gaps"]
    )
    assert report["items"][PREFIX + "grading-path"]["score"] == 1
    assert report["items"][PREFIX + "grading-dependency-access"]["score"] == 1


@pytest.mark.parametrize(
    "expression",
    [
        'eval("1")',
        'getattr(json, "loads")("{}")',
        "json.loads = print",
        'sys.path.append("/private")',
        'load_contract = print\nload_contract("/other")',
    ],
)
def test_static_closure_keeps_mutated_dispatch_explicit(tmp_path, expression):
    selection = refund_package(tmp_path)
    path = "tests/verify.py"
    refresh(tmp_path, selection, path, (tmp_path / path).read_text() + "\n" + expression + "\n")
    _, report = run_cli(tmp_path, selection)
    gaps = report["measurements"]["example_assessment"]["route_gaps"]
    assert any(not gap.endswith("unresolved-import") for gap in gaps)
