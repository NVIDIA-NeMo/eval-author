# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded Audit closure beside unchanged native producers and source reviews."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import io
import json
import os
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

completion: Any = importlib.import_module("current_rubric_completion")
support = importlib.import_module("current_rubric_support")
audit = importlib.import_module("rubric_audit_companion")
table = importlib.import_module("rubric_table")

REVISION = audit.REVISION


def remaining_gate() -> str:
    return "Run the three declared deterministic change-injection controls with stale rejection, unchanged neighbors and restored lineage. No new owner decision is required."


def attack_manifest(baseline: dict[str, Any]) -> dict[str, Any]:
    ids = {arm["native_id"] for arm in baseline["comparison"]["arms"]}
    rows = [row for row in baseline["result_revisions"] if row["id"] in ids]
    lineages = {row["lineage"] for row in rows}
    support.require(len(ids) == 2 and len(rows) == 2 and len(lineages) == 1, "ambiguous comparison lineage")
    manifests = [row for row in baseline["revision_manifests"] if row["index"] in lineages]
    support.require(len(manifests) == 1, "missing comparison lineage")
    manifest = manifests[0]
    support.require(all(row["recorded_revision"] == manifest["id"] for row in rows), "incompatible comparison lineage")
    return manifest


def identity() -> dict[str, str]:
    return {
        str(path): support.sha(path)
        for path in (Path(__file__), Path(completion.__file__), Path(str(support.__file__)), Path(audit.__file__))
    }


def copy_selection(root: Path, selection: dict[str, Any], output: Path) -> None:
    audit._copy_selection(root, selection, output)
    # Native task digests include directory modes; the frozen copier keeps only file modes.
    for path in sorted(output.rglob("*")):
        if path.is_dir():
            source = root / path.relative_to(output)
            support.require(source.is_dir() and not source.is_symlink(), "unsafe source directory")
            path.chmod(source.stat().st_mode & 0o777)


def neighbor_panel(root: Path, record: dict[str, Any]) -> list[dict[str, Any]]:
    task = str(Path(record["path"]).parent / "task")
    selected = table.select_task(root, task + "/task.toml")
    sources = audit.Sources(root, [*selected["artifacts"], record])
    panels = audit.project_panels(sources, {"native_bundle_ids": [record["id"]]}, task)
    support.require(len(panels) == 1 and panels[0]["task_freshness"] == "current", "unchanged neighbor invalidated")
    return panels


def exercise(root: Path, selection: dict[str, Any], output: Path) -> dict[str, Any]:
    support.require(
        not output.exists() and not any(p.is_symlink() for p in (output, *output.parents)), "unsafe Audit output"
    )
    baseline = audit.inspect(root, selection)
    manifest = attack_manifest(baseline)
    bundle = Path(manifest["index"]).parent
    output.mkdir(mode=0o700)
    completion.save(output / "baseline.json", baseline)
    affected = [row for row in baseline["result_revisions"] if row["lineage"] == manifest["index"]]
    neighbors = [row for row in baseline["result_revisions"] if row["lineage"] != manifest["index"]]
    tests = []
    recipes = (
        ("scoring-source", bundle / "task/tests/refund_scorer.py", b'grader == "baseline"', b'grader == "reviewed"'),
        ("runtime-budget", bundle / "task/task.toml", b"timeout_sec = 120", b"timeout_sec = 121"),
        ("observed-runtime", bundle / "trials/reference/config.json", None, None),
    )
    for name, relative, old, new in recipes:
        isolated = output / name
        copy_selection(root, selection, isolated)
        support.require(audit.inspect(isolated, selection) == baseline, "isolated baseline differs")
        target = isolated / relative
        original = support.raw(target)
        if old is None:
            value = json.loads(original)
            support.require(value.get("timeout_multiplier") != 2.0, "runtime mutation is a no-op")
            value["timeout_multiplier"] = 2.0
            changed = json.dumps(value).encode()
        else:
            if original.count(old) != 1 or new is None:
                raise ValueError("mutation source shape changed")
            changed = original.replace(old, new)
        target.write_bytes(changed)
        failure = None
        try:
            audit.inspect(isolated, selection)
        except ValueError as error:
            failure = str(error)
        support.require(failure is not None, "stale audit evidence was reused")
        invalidated = [
            {
                **row,
                "current_revision": None,
                "disposition": "invalidated",
                "reward": None,
                "checks": None,
                "original_result": row,
            }
            for row in affected
        ]
        completion.save(output / (name + "-invalidated.json"), invalidated)
        for neighbor in baseline["revision_manifests"]:
            if neighbor["index"] == manifest["index"]:
                continue
            record = next(row for row in selection["artifacts"] if row["path"] == neighbor["index"])
            support.require(
                neighbor_panel(isolated, record) == neighbor_panel(root, record),
                "unchanged neighbor projection differs",
            )
        counterpart = output / (name + "-fresh-counterpart")
        copy_selection(root, selection, counterpart)
        replay = audit.inspect(counterpart, selection)
        support.require(replay == baseline, "restored replay differs")
        support.require(
            support.raw(counterpart / relative) == original == support.raw(root / relative), "original changed"
        )
        tests.append(
            {
                "id": name,
                "mutation_path": relative.as_posix(),
                "stale_reuse_rejected": True,
                "first_rejection": failure,
                "invalidated_results": len(affected),
                "unchanged_results": len(neighbors),
                "fresh_source_observation": {
                    "sha256": hashlib.sha256(changed).hexdigest(),
                    "previous_sha256": hashlib.sha256(original).hexdigest(),
                    "native_score": None,
                },
                "restored_replay_equal": True,
                "neighbors_equal": True,
                "original_preserved": True,
                "restored_input_readback_sha256": support.sha(counterpart / relative),
                "recovery": "Exact fresh byte-matching rollback, not changed-source native regrading.",
            }
        )
    support.require(audit.inspect(root, selection) == baseline, "original admission changed")
    result = deepcopy(baseline)
    result["invalidation_tests"] = tests
    result["adapter_identity"] = identity()
    result["selection_sha256"] = audit.digest(selection)
    result["checks"][REVISION + ".deterministic-3"] = "PASS"
    # The owning anchor requires all bound checks, not a requested target score.
    outcomes = {k: v for k, v in result["checks"].items() if k.startswith(REVISION + ".")}
    if baseline["revision_score"] == 2 and set(outcomes.values()) <= {"PASS", "NOT_APPLICABLE"}:
        result["revision_score"] = 3
    result["limitations"].append(
        "Separately bound deterministic adapter. Three fixed changes reject stale native reuse and retain original lineage; rollback supplies no new native credit or source-review authority."
    )
    completion.save(output / "audit.json", result)
    return result


def tree_identity(root: Path) -> dict[str, Any]:
    return {
        "files": audit._tree_bytes(root),
        "directories": {
            p.relative_to(root).as_posix(): p.stat().st_mode & 0o777 for p in root.rglob("*") if p.is_dir()
        },
    }


def verify(root: Path, selection: dict[str, Any], output: Path) -> dict[str, Any]:
    retained = tree_identity(output)
    with tempfile.TemporaryDirectory(prefix="current-audit-verify-") as directory:
        fresh = Path(directory) / "exercise"
        result = exercise(root, selection, fresh)
        support.require(retained == tree_identity(fresh) == tree_identity(output), "retained Audit controls disagree")
    return result


@contextlib.contextmanager
def companion_context(options: dict[str, Any]):
    from rubric_upper_companions import environment_hook
    from rubric_upper_method_review import table_hook as method_hook
    from upper_anticheat_contract import table_hook

    with (
        support.trust_environment(
            Path(options["source-review-operator-store"]),
            options["source-review-operator-trust"],
            [Path(p) for p in options["registry"]],
        ),
        contextlib.ExitStack() as stack,
    ):
        stack.enter_context(environment_hook(Path(options["upper-environment-bundle"])))
        bundle, registry = Path(options["upper-anticheat-bundle"]), Path(options["upper-anticheat-registry"])
        stack.enter_context(table_hook(bundle, registry))
        stack.enter_context(method_hook(bundle, registry))
        yield


def project(report: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    projected = audit.project_report(report, result)
    output = deepcopy(report)
    output["items"][REVISION] = projected["items"][REVISION]
    output["measurements"]["audit_revision_closure"] = projected["measurements"]["audit_companion"]
    output["measurements"]["audit_revision_closure"]["inventory_ref"]["path"] = "../exercise/audit.json"
    row = output["items"][REVISION]
    for refs in [row["evidence_refs"], *[check["evidence_refs"] for check in row["check_results"]]]:
        for ref in refs:
            if ref.get("measurement") == "audit_companion" and ref.get("digest") == audit.digest(result):
                ref["measurement"] = "audit_revision_closure"
    # Fresh assessments and retained JSON have different mapping insertion order.
    return json.loads(table.encoded(output))


def ordinary(saved: dict[str, Any], options: dict[str, Any], output: Path) -> dict[str, Any]:
    output.mkdir(mode=0o700)
    selection_path = output / "selection.json"
    completion.save(selection_path, saved["selection"])
    with contextlib.redirect_stdout(io.StringIO()) as captured:
        status = table.main(
            ["--root", saved["root"], "--selection", str(selection_path), "--output", str(output / "ordinary")]
        )
    (output / "ordinary.stdout").write_text(captured.getvalue())
    support.require(status in (0, 1), "ordinary accepted-selection assessment failed")
    packet = support.document(output / "ordinary/assessment.json")
    support.require(
        packet["selection"] == saved["selection"] and packet["report"] == saved["report"],
        "ordinary base changed before Audit",
    )
    return packet


def emit(packet: dict[str, Any], result: dict[str, Any], output: Path) -> dict[str, Any]:
    value = {**packet, "report": project(packet["report"], result), "audit_closure_adapter": identity()}
    output.mkdir(mode=0o700)
    (output / "assessment.json").write_text(table.encoded(value) + "\n")
    completion.save(output / "audit.json", support.document(output.parent / "base/ordinary/audit.json"))
    text = table.render(value["report"], "Selected package")
    (output / "results.md").write_text(text)
    (output / "results.html").write_text(table.render_html(text))
    return value


def verify_existing(output: Path, matrix_path: Path, freeze: Path) -> dict[str, Any]:
    matrix = support.document(matrix_path)
    original = support.document(Path(matrix["base"]["report"]))
    packet = json.loads(table.read_private(output / "report/assessment.json"))
    support.require(packet["audit_closure_adapter"] == identity(), "Audit adapter identity changed")
    support.require(packet["selection"] == original["selection"], "accepted selection changed")
    options = matrix["base"]["options"]
    with companion_context(options):
        support.closure_check(freeze)
        result = verify(Path(original["root"]), original["selection"], output / "exercise")
        report = table.assess_table(Path(original["root"]), original["selection"], original["task"])
        support.require(report == original["report"], "ordinary retained source or owner readback changed")
        expected = {**original, "report": project(report, result), "audit_closure_adapter": identity()}
        support.require(packet == expected, "retained Audit assessment differs")
        markdown = table.render(expected["report"], "Selected package")
        support.require(
            table.read_private(output / "report/results.md").decode() == markdown, "retained Markdown differs"
        )
        support.require(
            table.read_private(output / "report/results.html").decode() == table.render_html(markdown),
            "retained HTML differs",
        )
        support.require(
            json.loads(table.read_private(output / "report/audit.json"))
            == audit.inspect(Path(original["root"]), original["selection"]),
            "original Audit inspection differs",
        )
        support.closure_check(freeze)
    return {
        "assessment": "verified",
        "controls": len(result["invalidation_tests"]),
        "score": expected["report"]["items"][REVISION]["score"],
    }


def close(args: argparse.Namespace) -> int:
    matrix = support.document(args.matrix)
    saved = support.document(Path(matrix["base"]["report"]))
    old_repair = support.document(Path(matrix["repair"]["report"]))
    support.require(saved == old_repair, "base and rollback packet differ; require separately scoped refresh")
    options = matrix["base"]["options"]
    support.require(options == matrix["repair"]["options"], "paired trust options differ")
    support.require(
        saved["selection"].get("audit_companion") == {"mode": "inspect"}, "requires unchanged inspect selection"
    )
    output = args.output.absolute()
    support.require(
        not output.exists() and not any(p.is_symlink() for p in (output, *output.parents)),
        "new private output required",
    )
    output.mkdir(mode=0o700, parents=True)
    store = Path(options["source-review-operator-store"])
    before = support.store_inventory(store)
    inputs = {str(Path(matrix[k]["report"])): support.sha(Path(matrix[k]["report"])) for k in ("base", "repair")}
    sources = identity()
    with companion_context(options):
        closures = support.closure_check(args.source_freeze)
        baseline = audit.inspect(Path(saved["root"]), saved["selection"])
        completion.save(
            output / "preflight.json", {"closures": closures, "lookup": attack_manifest(baseline), "sources": sources}
        )
        packet = ordinary(saved, options, output / "base")
        result = exercise(Path(saved["root"]), saved["selection"], output / "exercise")
        support.require(
            verify(Path(saved["root"]), saved["selection"], output / "exercise") == result, "Audit verification differs"
        )
        current = emit(packet, result, output / "report")
        # Final ordinary re-admission re-reads exact source/owner/upper bindings.
        replay = table.assess_table(Path(saved["root"]), saved["selection"], saved["task"])
        support.require(replay == saved["report"], "final ordinary readback changed")
        support.require(project(replay, result) == current["report"], "final projected readback differs")
        support.require(support.closure_check(args.source_freeze) == closures, "producer closure changed")
    support.require(identity() == sources, "adapter changed during exercise")
    support.require(support.store_inventory(store) == before, "owner store changed")
    support.require(all(support.sha(Path(p)) == h for p, h in inputs.items()), "original packets changed")
    changed = [k for k in saved["report"]["items"] if saved["report"]["items"][k] != current["report"]["items"][k]]
    support.require(changed == [REVISION], "neighbor row changed")
    record = {
        **matrix["base"],
        "report": str(output / "report/assessment.json"),
        "command": [sys.executable, "-B", str(Path(__file__)), *sys.argv[1:]],
        "audit_closure": str(output / "exercise/audit.json"),
    }
    updated = completion.runner.matrix_summary(record, deepcopy(matrix["runs"]), record)
    updated.update(
        options=matrix["options"],
        supporting_reports=matrix.get("supporting_reports", []),
        owning_matrix=str(args.matrix),
        owning_matrix_sha256=support.sha(args.matrix),
    )
    missing_diffs = audit.typed_diff(matrix["missing_evidence_cells"], updated["missing_evidence_cells"])
    for delta in missing_diffs:
        field = delta["pointer"].rsplit("/", 1)[-1]
        cell = matrix["missing_evidence_cells"][int(delta["pointer"].split("/")[1])]
        support.require(
            (
                field == "base_report"
                and delta["left"] == matrix["base"]["report"]
                and delta["right"] == record["report"]
            )
            or (
                field == "repair_report"
                and delta["left"] == matrix["repair"]["report"]
                and delta["right"] == record["report"]
            )
            or (
                cell["criterion"] == REVISION
                and field in {"base", "repair"}
                and delta["left"] == 2
                and delta["right"] == current["report"]["items"][REVISION]["score"]
            ),
            "missing-evidence outcome changed",
        )
    value = completion.coverage(updated)
    support.require(
        value["lower_numeric_observed"] == 63 and value["missing_evidence_drops"] == 21,
        "lower or missing coverage changed",
    )
    crosswalk = support.document(args.matrix.with_name("decision-crosswalk.json"))
    with support.trust_environment(
        store, options["source-review-operator-trust"], [Path(p) for p in options["registry"]]
    ):
        support.validate_crosswalk(crosswalk, store)
    verification = {
        "source_hashes": sources,
        "producer_closures": closures,
        "changed_rows": changed,
        "neighbor_rows_unchanged": len(current["report"]["items"]) - len(changed),
        "selection_equal": True,
        "owner_store_equal": True,
        "upper_bindings_equal": True,
        "three_controls_verified": True,
        "base_and_repair_share_exact_verified_rollback": True,
        "ordinary_reports_regenerated": 1,
        "full_matrix_replay": "not_run",
        "native_launches": 0,
        "model_launches": 0,
        "inputs": inputs,
    }
    value["verification"].update(verification)
    for name, data in (
        ("matrix.json", updated),
        ("coverage.json", value),
        ("decision-crosswalk.json", crosswalk),
        ("verification.json", verification),
    ):
        completion.save(output / name, data)
    (output / "summary.md").write_text(completion.render(value, crosswalk, output))
    completion.save(output / "generation.json", {"argv": sys.argv, "options": options, "source_hashes": sources})
    print(
        json.dumps(
            {
                "output": str(output),
                "base": value["base_score_counts"],
                "numeric": value["numeric_observed"],
                "lower": value["lower_numeric_observed"],
                "missing": value["missing_evidence_drops"],
            }
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--source-freeze", type=Path, required=True)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--verify-existing", type=Path)
    parser.add_argument("--approve-audit-exercise", action="store_true", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.verify_existing:
        print(json.dumps(verify_existing(args.verify_existing, args.matrix, args.source_freeze)))
        return 0
    return close(args)


if __name__ == "__main__":
    raise SystemExit(main())
