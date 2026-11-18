# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Retained-evidence orchestration outside the frozen rubric producer trees."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

c: Any = importlib.import_module("current_rubric_completion")

# These are reviewed executable implementations, not evidence-supplied entrypoints.
REVIEWED = {
    "qualification": (
        "qualification",
        "qualification.py",
        "66993c17995a7fb25a9696e9877487014254d8b31b301eabbcaa446bca4203a4",
    ),
    "upper_companion": (
        "upper",
        "upper_companion.py",
        "b78fcf7d4a684731ce09b660b54c9e322c994143a76bcf743b56118e8bd72d24",
    ),
    "ordinary_adapter": (
        "upper",
        "ordinary_adapter.py",
        "146c783c4ed5cea9a6258d0fcf2e6c82d80b62ab65cd91e819cd32911de35db6",
    ),
    "closure": ("native", "closure.py", "cd9ba5ef1be0b1d168aad7be91ae43d4703c4bed5e85710d88de594d2deae33a"),
    "admission": ("native", "admission.py", "8643e8f901ceeb3ec5365324828b211558417dcb12b326e8d08db45be48eea4a"),
    "candidate_boundary": (
        "native",
        "candidate_boundary.py",
        "64ff93bca2a9df550fcb7a81fbd1ff25f1c8b285699c1130c65738a17f09266b",
    ),
    "observation_reconciliation": (
        "native",
        "observation_reconciliation.py",
        "dd17767aad0a5b03bf6ae52853b76b01c10ce45feb9d7c2d9846849877666ad3",
    ),
}
ORACLE_SHA = "57c679cdff5103906825357c519a6e4dbe991c1be8813b4b9569e119c7f6ff90"
REVIEWED_REPORTS = {
    "qualification": "cc555f751458772b88fee65112110699b86cee5867de0abf3e6143c0ecdb0b85",
    "native": "1229386969cb6a5baddc9c3372734699fed98eac5273e080376f290ea07ed75f",
}
PRODUCERS = {
    "access": "rubric_refund_access",
    "attacks": "rubric_refund_panel",
    "boundaries": "rubric_refund_boundaries",
    "heldout": "rubric_refund_holdout",
    "runtime": "rubric_native_collect",
    "upper-environment": "rubric_upper_environment",
    "native-numeric": "native_numeric_mutations",
    "upper-ac": "upper_anticheat_contract",
}
SOURCE_ROWS = (
    "provenance.task-artifact-access",
    "provenance.grading-path",
    "provenance.grading-dependency-access",
    "provenance.declared-configuration",
)


def require(condition: Any, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def raw(path: Path) -> bytes:
    require(not any(p.is_symlink() for p in (path, *path.parents)), "symlink in retained input")
    require(path.is_file(), "missing retained input: " + str(path))
    return path.read_bytes()


def sha(path: Path) -> str:
    return hashlib.sha256(raw(path)).hexdigest()


def document(path: Path) -> Any:
    return json.loads(raw(path))


def closure_check(freeze_path: Path) -> dict[str, bool]:
    freeze = document(freeze_path)
    checks = {}
    for name, module in PRODUCERS.items():
        owner = importlib.import_module(module)
        actual = owner.identity() if name == "upper-ac" else owner.producer_identity()
        checks[name] = actual == freeze["producer_identities"][name]
    require(all(checks.values()), "frozen native producer closure changed")
    return checks


def matrix_lineage(path: Path) -> list[tuple[Path, dict[str, Any]]]:
    result = []
    seen = set()
    while True:
        require(path not in seen, "matrix lineage cycle")
        seen.add(path)
        matrix = document(path)
        require(matrix["definitions_sha256"] == c.runner.definitions_digest(), "canonical definitions changed")
        result.append((path, matrix))
        if not matrix.get("owning_matrix"):
            return result
        parent = Path(matrix["owning_matrix"])
        require(sha(parent) == matrix["owning_matrix_sha256"], "owning matrix changed")
        path = parent


def records(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    values = [matrix["base"], matrix["repair"], *matrix.get("supporting_reports", [])]
    for run in matrix["runs"]:
        values.extend(run[key] for key in ("assessment", "criterion_baseline", "criterion_repair") if key in run)
    return list({str(Path(value["report"]).resolve()): value for value in values}.values())


def retained_inputs(matrix: dict[str, Any]) -> dict[str, str]:
    inputs = {}
    for record in records(matrix):
        path = Path(record["report"])
        inputs[str(path)] = sha(path)
        saved = document(path)
        measurements = saved["report"]["measurements"] if "measurements" in saved["report"] else {}
        leakage = measurements.get("leakage_bounded")
        if leakage and leakage.get("reviews", {}).get("owner"):
            owner = importlib.import_module("rubric_leakage_intake")
            receipt = owner.read(leakage, root=Path(saved["root"]), selection=saved["selection"])
            require(
                receipt is not None and receipt == leakage["reviews"]["owner"] and not receipt["revoked"],
                "retained leakage owner admission changed",
            )
        require(
            set(saved["report"]["items"]) == set(c.runner.canonical_definitions()), "retained canonical rows incomplete"
        )
        for artifact in saved["selection"]["artifacts"]:
            if artifact.get("access") != "available":
                continue
            relative = Path(artifact["path"])
            require(not relative.is_absolute() and ".." not in relative.parts, "unsafe selection path")
            selected = Path(saved["root"]) / relative
            actual = sha(selected)
            require(actual == artifact["digest"], "selected evidence changed: " + str(selected))
            inputs[str(selected)] = actual
    return inputs


def store_inventory(store: Path) -> dict[str, dict[str, Any]]:
    require(store.is_dir() and not store.is_symlink(), "operator store unavailable")
    return {
        str(p): {"sha256": sha(p), "mode": p.stat().st_mode & 0o777} for p in sorted(store.iterdir()) if p.is_file()
    }


def validate_crosswalk(crosswalk: dict[str, Any], store: Path) -> None:
    require(crosswalk["rows"], "missing decision crosswalk")
    for row in crosswalk["rows"]:
        receipt = row.get("current_receipt")
        require(receipt and row["status"] == "retained", "pending exact owner decision: " + row["kind"])
        request = receipt["event"]["request"]
        require(request["binding"] == row["binding"], "crosswalk binding differs")
        matches = [p for p in store.iterdir() if p.is_file() and sha(p) == receipt["digest"]]
        require(len(matches) == 1, "exact owner event absent or ambiguous")
        path = matches[0]
        require(not path.with_suffix(".revoked").exists(), "owner event revoked")
        saved = document(path)
        require(
            saved["event"] == receipt["event"] and saved["trust_basis"] == receipt["trust_basis"], "owner event changed"
        )


@contextlib.contextmanager
def trust_environment(store: Path, trust: str, registries: list[Path]) -> Iterator[None]:
    values = {
        "EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE": str(store),
        "EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST": trust,
        "EVAL_AUTHOR_NATIVE_REGISTRIES": os.pathsep.join(map(str, registries)),
        "EVAL_AUTHOR_SOURCE_REVIEW_SESSION_STORE": None,
        "EVAL_AUTHOR_SOURCE_REVIEW_TRUST": None,
    }
    before = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
        yield
    finally:
        for key, value in before.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value


def reviewed_buffers(roots: dict[str, Path]) -> dict[str, tuple[Path, bytes]]:
    result = {}
    for name, (root, relative, expected) in REVIEWED.items():
        path = roots[root] / relative
        source = raw(path)
        require(hashlib.sha256(source).hexdigest() == expected, "unreviewed companion implementation: " + name)
        result[name] = (path, source)
    require(sha(roots["upper"] / "public/independent_audit.py") == ORACLE_SHA, "unreviewed companion oracle")
    for key, expected in REVIEWED_REPORTS.items():
        require(sha(roots[key] / "ordinary-report/assessment.json") == expected, "reviewed companion report changed")
    return result


@contextlib.contextmanager
def reviewed_modules(roots: dict[str, Path]) -> Iterator[dict[str, ModuleType]]:
    buffers = reviewed_buffers(roots)
    previous_path = sys.path[:]
    previous_modules = {key: sys.modules.get(key) for key in buffers}
    loaded = {}
    try:
        # Preload the closed dependency set. No evidence-provided module names or argv.
        for name, (path, _) in buffers.items():
            module = ModuleType(name)
            module.__file__ = str(path)
            loaded[name] = module
            sys.modules[name] = module
        for name in (
            "candidate_boundary",
            "closure",
            "admission",
            "observation_reconciliation",
            "upper_companion",
            "ordinary_adapter",
            "qualification",
        ):
            path, source = buffers[name]
            exec(compile(source, str(path), "exec"), loaded[name].__dict__)
        q = loaded["qualification"]
        require(
            q.REPO == c.SCRIPTS.parents[2] and q.HERE == roots["qualification"] and q.UPPER == roots["upper"],
            "reviewed companion is path-bound; restore original paths, do not migrate/reseal",
        )
        require(loaded["closure"].REPO == c.SCRIPTS.parents[2], "native companion checkout differs")
        require(loaded["candidate_boundary"].OLD == roots["upper"], "native companion population differs")
        yield loaded
        require(reviewed_buffers(roots) == buffers, "companion implementation changed during use")
    finally:
        sys.path[:] = previous_path
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def integrate_support(matrix: dict[str, Any], output: Path, roots: dict[str, Path]) -> list[dict[str, Any]]:
    with reviewed_modules(roots) as modules:
        q = modules["qualification"]
        nc = modules["observation_reconciliation"]
        validated, receipts = q.admit_qualification()
        predicates = q.predicates(validated)
        require(
            all(
                predicates[key]
                for key in (
                    "new_exposure_bounded_and_sensitivity",
                    "new_finite_cue_controls",
                    "new_exploratory_history",
                    "new_declared_reset",
                    "copied_exposure_bounded_and_sensitivity",
                )
            ),
            "companion prerequisites incomplete",
        )
        upper_path = roots["qualification"] / "ordinary-report/assessment.json"
        upper = document(upper_path)
        qualification = upper["report"]["measurements"]["upper_qualification"]
        require(
            qualification["receipts"] == receipts and qualification["predicates"] == predicates,
            "upper report admission differs",
        )
        require(
            upper["report"]["measurements"]["external_upper_controls"] == validated[2], "upper raw projection differs"
        )
        require(
            {k: r["score"] for k, r in upper["report"]["items"].items() if r["score"] is not None}
            == {"AUDIT-03": 1, "AUDIT-05-B": 3, "AUDIT-05-S03": 2, "CREATE-02": 2, "ENV-03-S01": 2},
            "upper scoped scores differ",
        )
        run = next(r for r in matrix["runs"] if r["case"] == "leakage-AUDIT-05-B.base")
        fresh = document(Path(run["assessment"]["report"]))
        after = q.apply_existing_provenance(
            fresh["report"],
            population_projection=fresh["report"]["measurements"]["leakage_bounded"],
            root=Path(fresh["root"]),
            selection=fresh["selection"],
        )
        require(after["items"]["AUDIT-05-B"]["score"] == 3, "copied population prerequisites incomplete")
        require(
            all(after["items"][k] == value for k, value in fresh["report"]["items"].items() if k != "AUDIT-05-B"),
            "unrelated copied-population row changed",
        )
        dest = output / "copied-population"
        dest.mkdir(mode=0o700)
        packet = {
            **fresh,
            "report": after,
            "retained_replay": "DEFERRED",
            "consumer_adapter": str(roots["qualification"] / "qualification.py"),
        }
        c.save(dest / "assessment.json", packet)
        md = q.rubric_table.render(after, "Exact copied population — qualified provenance, not original Harbor base")
        (dest / "results.md").write_text(md)
        (dest / "results.html").write_text(q.rubric_table.render_html(md))
        native = nc.reconcile()
        native_path = roots["native"] / "ordinary-report/assessment.json"
        stored = document(native_path)
        require(
            native == stored["report"]["measurements"]["external_native_closure"], "native support projection differs"
        )
        require(
            all(row["score"] is None for row in stored["report"]["items"].values()),
            "native support cannot supply scores",
        )
        require(
            q.admit_qualification()[1] == receipts and nc.reconcile() == native, "final companion admission changed"
        )
        matrix["supporting_reports"] = [
            {"case": "qualified-copied-population", "report": str(dest / "assessment.json"), "state": "assessed"},
            {"case": "qualified-new-families", "report": str(upper_path), "state": "assessed"},
        ]
        return [
            {
                "title": "Qualified separate populations",
                "report": str(upper_path),
                "receipts": receipts,
                "predicates": predicates,
                "summary": "Exact UQ1/UQ2 records and owning raw evidence admitted. Separate new-family and copied-reference populations do not promote the original Harbor base or inherit its repair. Full exposure and source dissent remain in owning reports.",
            },
            {
                "title": "Candidate-alias native follow-up",
                "report": str(native_path),
                "projection": native,
                "summary": f"{len(native['rows'])} controls, {len({row['container'] for row in native['rows']})} distinct containers, validated from raw receipts. Alias predictions were not reproduced: unchanged and no-follow scorers reject aliases; public-evidence controls succeed. Four original-service controls use installed Harbor Verifier, not full Trial. The historical unexecuted label is superseded only here. No confirmed exploit, defect zero or repair benefit follows. No cross-container scheduler is declared; no queue repair is required. Standalone rubric scores remain Unassessed.",
            },
        ]


def reuse_current(args: argparse.Namespace) -> int:
    require(
        all((args.source_freeze, args.source_review_operator_store, args.source_review_operator_trust, args.registry)),
        "reuse requires --source-freeze, explicit operator store/trust and --registry",
    )
    require(
        not any(
            (
                args.refresh_decisions,
                args.create02_matrix,
                args.source_report,
                args.intake,
                args.request_deltas,
                args.upper_evidence_root,
            )
        ),
        "reuse cannot be combined with initial-generation or decision-refresh flags",
    )
    roots = {"qualification": args.qualification_root, "upper": args.upper_root, "native": args.native_followup_root}
    require(
        bool(args.reviewed_companions) == any(roots.values()),
        "companion roots require explicit --reviewed-companions finite-upper-v1",
    )
    if args.reviewed_companions:
        require(all(roots.values()), "all three companion roots required")
        roots = {key: value.absolute() for key, value in roots.items()}
        reviewed_buffers(roots)
    scratch = Path(os.environ.get("TMPDIR", ""))
    require(
        bool(os.environ.get("TMPDIR"))
        and scratch.is_absolute()
        and scratch.is_dir()
        and not any(p.is_symlink() for p in (scratch, *scratch.parents)),
        "TMPDIR must name an existing private nonsymlink scratch directory",
    )
    output = args.output.absolute()
    require(
        not output.exists() and not any(p.is_symlink() for p in (output, *output.parents)),
        "output must be a new nonsymlink directory",
    )
    lineage = matrix_lineage(args.matrix.absolute())
    matrix = deepcopy(lineage[0][1])
    require(
        not matrix.get("supporting_reports") or args.reviewed_companions,
        "supporting reports require their owning reviewed companion option",
    )
    store = args.source_review_operator_store.absolute()
    crosswalk = document(args.matrix.with_name("decision-crosswalk.json"))
    with trust_environment(store, args.source_review_operator_trust, [p.absolute() for p in args.registry]):
        before_store = store_inventory(store)
        checks = closure_check(args.source_freeze)
        inputs = retained_inputs(matrix)
        validate_crosswalk(crosswalk, store)
        native_matrix = next((m for _, m in lineage if m.get("native_bundle")), None)
        if native_matrix is None:
            raise ValueError("native evidence lineage missing")
        require(
            Path(native_matrix["native_registry"]).absolute() in [p.absolute() for p in args.registry],
            "native lineage registry was not explicitly authorized",
        )
        require(
            c.extensions.native_admission(Path(native_matrix["native_bundle"]), Path(native_matrix["native_registry"]))
            == native_matrix["native_admission"],
            "native admission differs",
        )
        create_matrix = next((m["create02_matrix"] for _, m in lineage if m.get("create02_matrix")), None)
        if create_matrix is None:
            raise ValueError("CREATE-02 evidence lineage missing")
        require(
            document(Path(create_matrix).with_name("source-freeze.json")) == c.create02.identity(),
            "CREATE-02 closure changed",
        )
        saved = document(Path(matrix["base"]["report"]))
        require(
            output.is_relative_to(Path(saved["root"])),
            "output must be inside the original base root for ordinary assessment",
        )
        output.mkdir(parents=True, mode=0o700)
        options = deepcopy(matrix["base"]["options"])
        require(
            set(options)
            <= {
                "registry",
                "source-review-operator-store",
                "source-review-operator-trust",
                "upper-anticheat-bundle",
                "upper-anticheat-registry",
                "upper-environment-bundle",
            },
            "unsupported retained orchestration option",
        )
        if options.get("upper-anticheat-registry"):
            require(
                Path(options["upper-anticheat-registry"]).absolute() in [p.absolute() for p in args.registry],
                "companion registry was not explicitly authorized",
            )
        options.update(
            {
                "source-review-operator-store": str(store),
                "source-review-operator-trust": args.source_review_operator_trust,
                "registry": [str(p.absolute()) for p in args.registry],
            }
        )
        fresh = c.runner.matrix_assess(Path(saved["root"]), saved["selection"], output / "base-validation", options)
        require(fresh["exit"] in (0, 1), "ordinary base validation failed")
        current = document(Path(fresh["report"]))
        require(current["selection"] == saved["selection"], "ordinary selection changed")
        require(
            current["report"] == saved["report"],
            "base measurements or rows changed; refresh exact affected evidence before reuse",
        )
        supports = integrate_support(matrix, output, roots) if args.reviewed_companions else []
        value = c.coverage(matrix)
        value["validated_supporting_evidence"] = supports
        note = "Missing-evidence drops can share broad withholding; they are not independently isolated removals. Criterion-local leakage baselines remain separate from the original base."
        value["missing_evidence_scope_note"] = note
        value["limits"].append(note)
        if supports:
            value["base_gaps"]["ENV-03-S01"].append(
                "No cross-container scheduler is declared. Material-surface and independently authored corroboration gaps remain; no queue repair is required."
            )
        validate_crosswalk(crosswalk, store)
        require(store_inventory(store) == before_store, "operator store changed during reuse")
        require(
            all(sha(Path(path)) == expected for path, expected in inputs.items()), "retained input changed during reuse"
        )
        require(closure_check(args.source_freeze) == checks, "source closure changed during reuse")
        matrix.update(owning_matrix=str(args.matrix.absolute()), owning_matrix_sha256=sha(args.matrix.absolute()))
        verification = {
            "producer_closures": checks,
            "create02_identity_equal": True,
            "source_four_rows_exactly_unchanged": list(SOURCE_ROWS),
            "all_base_rows_exactly_unchanged": True,
            "retained_reports": len(records(lineage[0][1])),
            "retained_input_files": len(inputs),
            "ordinary_base_validation": fresh,
            "ordinary_reports_regenerated": 1,
            "native_launches": 0,
            "model_launches": 0,
            "new_owner_decisions": 0,
            "manual_interventions": 0,
            "manual_intervention_scope": "This retained-evidence command after explicit inputs; excludes historical qualification and setup, not a fresh-user study.",
            "retained_replay": "deferred",
            "full_tests": "not_run",
        }
        value["verification"].update(verification)
        for name, data in (
            ("matrix.json", matrix),
            ("coverage.json", value),
            ("decision-crosswalk.json", crosswalk),
            ("verification.json", verification),
            ("reuse-inputs.json", inputs),
        ):
            c.save(output / name, data)
        (output / "summary.md").write_text(c.render(value, crosswalk, output))
        c.save(
            output / "generation.json",
            {
                "argv": sys.argv,
                "completion_sha256": sha(Path(c.__file__)),
                "support_sha256": sha(Path(__file__)),
                "reviewed_companions": args.reviewed_companions,
                "native_launches": 0,
                "model_launches": 0,
                "retained_replay": "deferred",
            },
        )
        print(
            json.dumps(
                {
                    "report": str(output / "summary.md"),
                    "numeric_observed": value["numeric_observed"],
                    "lower_numeric_observed": value["lower_numeric_observed"],
                    "missing_drops": value["missing_evidence_drops"],
                    "base": value["base_score_counts"],
                    "manual_interventions": 0,
                    "ordinary_reports_regenerated": 1,
                }
            )
        )
    return 0
