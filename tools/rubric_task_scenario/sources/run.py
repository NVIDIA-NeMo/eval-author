# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""One command: collect real local controls, assess supplied fictional world, render, verify.
No network, models, credentials, Docker, installs, account/config or empirical-store writes.
"""

import argparse
import copy
import json
import os
import sys
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load(name):
    p = ROOT / (name + ".py")
    m = types.ModuleType(name)
    m.__file__ = str(p)
    exec(compile(p.read_bytes(), str(p), "exec"), m.__dict__)
    return m


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--assess-existing",
        action="store_true",
        help="Assess an already collected private run, retaining its raw observations",
    )
    args = parser.parse_args()
    out = args.output.resolve()
    if out.parent != ROOT:
        raise ValueError("output must be direct child of this private package")
    os.umask(0o077)
    a = load("assess")
    c = load("collect")
    manifest = a.source_manifest()
    bundled = a.read(ROOT / "bundled-source-identity.json")["files"]
    assert manifest == bundled, "bundled source identity mismatch"
    if not args.assess_existing:
        c.run(out)
    elif not (out / "collection-summary.json").is_file():
        raise ValueError("existing collection incomplete")
    if (out / "assessment.json").exists():
        raise ValueError("assessment output already exists; never overwrite an attempt")
    a.save(
        out / "source-manifest.json",
        {
            "files": manifest,
            "binding_scope": "all task/control/assessor/canonical/premise sources; actual imported runtime inventory separate",
            "freeze_timing": "before this collection"
            if not args.assess_existing
            else "postcollection retrospective assessment; source data present in collector output",
        },
    )
    a.supplement(out, manifest)
    files = {str(p.relative_to(out)): a.sha(p.read_bytes()) for p in sorted(out.rglob("*")) if p.is_file()}
    a.save(out / "evidence-manifest.json", {"files": files, "root": str(out), "empirical_admission": False})
    evidence = a.gather(out)
    premises = a._dispositions.bind_templates(a.read(ROOT / "scenario-premises.json"), evidence, manifest)
    a.save(out / "bound-premises.json", premises)
    report = a.assess(evidence, premises, binding_valid=manifest == a.source_manifest())
    revision = a.sha(a.packed(manifest))
    for row in report["items"].values():
        row["subject_revision"] = revision
        for check in row["check_results"]:
            for ref in check["evidence_refs"] + check["method_qualification_refs"]:
                path = (out / ref["path"]).resolve()
                if not path.is_relative_to(ROOT):
                    raise ValueError("citation escapes private package")
                ref["sha256"] = a.sha(path.read_bytes())
                obj = a.read(path)
                for part in ref["pointer"].strip("/").split("/") if ref["pointer"] else []:
                    part = part.replace("~1", "/").replace("~0", "~")
                    obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    a.save(out / "assessment.json", {"report": report})
    a.render(report, out)
    # Read back emitted rows, canonical inventories, all citations and no target scores.
    emitted = a.read(out / "assessment.json")["report"]
    defs, _ = a.definitions()
    assert list(emitted["items"]) == sorted(defs) or set(emitted["items"]) == set(defs)
    inspected = []
    for ident, row in emitted["items"].items():
        assert row["canonical_anchors"] == defs[ident]["scoring"]
        assert [x["check_id"] for x in row["check_results"]] == [x["id"] for x in defs[ident]["checks"]]
        assert row["approval_refs"] == [] and row["target_score"] is None
        assert all(x["evidence_refs"] for x in row["check_results"])
        inspected.append(
            {
                "item_id": ident,
                "score": row["score"],
                "status": row["assessment_state"],
                "checks": len(row["check_results"]),
                "outcomes": dict(a.Counter(x["outcome"] for x in row["check_results"])),
                "remaining_gaps": row["remaining_gaps"],
            }
        )
    # Targeted adversarial tests of assessment consumption, not a desired-score input.
    tests = []
    bad = copy.deepcopy(evidence)
    bad["observations"][0]["result"]["checks"]["amount"] = 0
    changed = a.assess(bad, premises)
    tests.append(
        {
            "case": "changed actual per-check result cannot inherit top score",
            "passed": changed["items"]["task-contract.scoring-authority"]["score"] != 3,
        }
    )
    bad = copy.deepcopy(evidence)
    bad["gain"]["difference"] = 0
    tests.append(
        {
            "case": "zero gain cannot receive adaptive top anchor",
            "passed": a.assess(bad, premises)["items"]["AUDIT-05-S03"]["score"] != 3,
        }
    )
    bad = copy.deepcopy(evidence)
    bad["gain"]["difference"] = None
    tests.append(
        {
            "case": "null gain cannot receive adaptive top anchor",
            "passed": a.assess(bad, premises)["items"]["AUDIT-05-S03"]["score"] != 3,
        }
    )
    bad = copy.deepcopy(evidence)
    bad["gain"]["difference"] = 0.99
    tests.append(
        {
            "case": "invented positive gain rejected by raw outcome reconstruction",
            "passed": a.assess(bad, premises)["items"]["AUDIT-05-S03"]["score"] != 3,
        }
    )
    bad = copy.deepcopy(evidence)
    bad["selection"]["lineage"][0]["feedback"] = not bad["selection"]["lineage"][0]["feedback"]
    tests.append(
        {
            "case": "altered feedback rejected by observation reconciliation",
            "passed": a.assess(bad, premises)["items"]["AUDIT-05-S03"]["score"] != 3,
        }
    )
    bad = copy.deepcopy(premises)
    del bad["reviews"]["discovery"]
    tests.append(
        {
            "case": "missing fictional discovery premise loses top anchor",
            "passed": a.assess(evidence, bad)["items"]["task-contract.discoverable-requirements"]["score"] != 3,
        }
    )
    bad = copy.deepcopy(premises)
    bad["schema"] = "empirical"
    try:
        a.assess(evidence, bad)
    except ValueError:
        tests.append({"case": "fiction cannot be admitted by removing scope tag", "passed": True})
    else:
        tests.append({"case": "fiction cannot be admitted by removing scope tag", "passed": False})
    stale = a.assess(evidence, premises, binding_valid=False)
    tests.append(
        {
            "case": "source drift invalidates revision score",
            "passed": stale["items"]["provenance.evidence-revision-consistency"]["score"] is None,
        }
    )
    bad = copy.deepcopy(evidence)
    bad["cues"][next(i for i, x in enumerate(bad["cues"]) if x["class"] == "style")]["transition"] = [True, False]
    tests.append(
        {
            "case": "failed style control is consumed",
            "passed": a.assess(bad, premises)["items"]["CREATE-02"]["score"] != 3,
        }
    )
    bad = copy.deepcopy(evidence)
    bad["cues"] = [x for x in bad["cues"] if not (x["family"] == "dependency-release" and x["class"] == "style")]
    # Rebind the fictional review to the changed packet so only the evidence gap differs.
    rebound = a._dispositions.bind_templates(a.read(ROOT / "scenario-premises.json"), bad, manifest)
    companion_gap = a.assess(bad, rebound)["items"]
    tests.append(
        {
            "case": "companion-family gap moves only the suite row",
            "passed": companion_gap["CREATE-02"]["score"] == 3 and companion_gap["CREATE-02-SUITE"]["score"] != 3,
        }
    )
    bad = copy.deepcopy(premises)
    bad["expected_scores"] = {x: 3 for x in defs}
    try:
        a.assess(evidence, bad)
    except ValueError:
        tests.append({"case": "expected score input rejected", "passed": True})
    else:
        tests.append({"case": "expected score input rejected", "passed": False})
    a.save(out / "assessment-negative-tests.json", tests)
    assert all(x["passed"] for x in tests), tests
    # Actual full rows on retained readback equal rerun assessment (citation decoration excluded).
    repeated = a.assess(
        a.gather(out), a.read(out / "bound-premises.json"), binding_valid=manifest == a.source_manifest()
    )

    def undecorate(row):
        row = copy.deepcopy(row)
        row["subject_revision"] = None
        for ref in row["evidence_refs"] + row["method_qualification_refs"]:
            ref.pop("sha256", None)
        for ch in row["check_results"]:
            for ref in ch["evidence_refs"] + ch["method_qualification_refs"]:
                ref.pop("sha256", None)
        return row

    assert all(undecorate(row) == undecorate(repeated["items"][ident]) for ident, row in emitted["items"].items())
    assert all(a.sha((out / name).read_bytes()) == digest for name, digest in files.items())
    assert manifest == a.source_manifest()
    assert a.source_manifest() == bundled, "bundled source changed during execution"
    counts = a.Counter(
        "N/A" if r["assessment_state"] == "NOT_APPLICABLE" else "Unassessed" if r["score"] is None else str(r["score"])
        for r in emitted["items"].values()
    )
    a.save(out / "row-inspection.json", inspected)
    md = (out / "report.md").read_text()
    html = (out / "report.html").read_text()
    assert len([line for line in md.splitlines() if line.startswith("| Fictional |")]) == len(defs)
    assert all(ident in html for ident in defs) and "SUPPLIED SIMULATION PREMISES" in html
    final = {
        "canonical_rows": len(emitted["items"]),
        "applicable_rows": sum(r["assessment_state"] != "NOT_APPLICABLE" for r in emitted["items"].values()),
        "not_applicable_rows": sum(r["assessment_state"] == "NOT_APPLICABLE" for r in emitted["items"].values()),
        "top_anchor_rows": sum(r["score"] == 3 for r in emitted["items"].values()),
        "canonical_checks": sum(len(x["check_results"]) for x in emitted["items"].values()),
        "status_counts": dict(counts),
        "full_row_reassessment_equal": True,
        "all_citations_resolved": True,
        "negative_tests_passed": len(tests),
        "bundled_source_identity_verified": True,
        "historical_integrity_claimed": False,
        "source_and_evidence_unchanged": True,
        "renderer_rows_verified": True,
        "actual_collection": evidence["summary"],
        "empirical_qualification": False,
        "all_applicable_top_anchors": all(
            r["score"] == 3 or r["assessment_state"] == "NOT_APPLICABLE" for r in emitted["items"].values()
        ),
    }
    a.save(out / "FINAL-VERIFICATION.json", final)
    print(json.dumps(final, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Keep every failed attempt's raw files and exact Python failure.
        if len(sys.argv) > 1:
            try:
                p = Path(sys.argv[1]).resolve()
                if p.parent == ROOT and p.exists():
                    with (p / "RUN-FAILURE.txt").open("x") as stream:
                        stream.write(traceback.format_exc())
            except (OSError, ValueError):
                pass
        raise
