# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compare offline summaries, not independently verified native evidence.

Hashes and CI identifiers are untrusted claims: duplicate checks detect reuse,
not forged evidence or independence. Local input order is not chronology. Plans
and incomplete runs remain visible; missing outcomes never become zero scores.
No raw traces are read, no trusted CI ingestion or statistical inference occurs.
Provider hashes record observed labels, not immutable deployment proof; unknown
revisions stay unknown. Runtime extensions split series without being published.
CLI: python tools/compare_anti_cheat_live.py SUMMARY SUMMARY [SUMMARY ...]
Exit 0 means consistent summaries, not passing experiments; exit 2 rejects input.
"""

import json
import re
import sys
from pathlib import Path

from run_anti_cheat_live import (
    ARMS,
    CONTROLS,
    canonical,
    cells,
    controls_healthy,
    expected,
    hash_value,
    integer,
    load_json,
    metrics,
    require,
    sha,
)


def validate(report):
    require(report["schema"] == "anti-cheat-live-v1" and report["protocol"] == "paired-repair-v1")
    mode, rows = report["mode"], report["cells"]
    require(mode in ("plan", "controls", "live") and isinstance(rows, list))
    repetitions = 1 if mode == "controls" else (len(rows) - 6) // 4
    integer(repetitions, 1, 10)
    topology = cells(mode, repetitions)
    require(len(rows) == len(topology))
    require(canonical(report["expected"]) == canonical(expected(mode, repetitions)))
    denominators, hashes = {}, []
    for row, identity in zip(rows, topology):
        require(canonical({key: row[key] for key in identity}) == canonical(identity))
        require(row["status"] in ("completed", "pending", "incomplete"))
        usage_fields = ("observed_tokens", "observed_turns", "observed_tool_calls")
        # Legacy summaries may omit the entire usage block, but never part of it.
        if {"usage_status", *usage_fields}.intersection(row):
            usage = row["usage_status"]
            require(
                usage
                in ("not_observed", "not_applicable", "partial_observed_not_total_billed", "observed_not_total_billed")
            )
            counters = [row[field] for field in usage_fields]
            for value in counters:
                if value is not None:
                    integer(value, 0, 1000000000)
            if usage == "not_observed":
                require(all(value is None for value in counters) or counters == [0, 0, 0])
                require(not row.get("observed_models"))
            else:
                require(all(value is not None for value in counters))
            if usage == "not_applicable":
                require(row["arm"] in CONTROLS and counters == [0, 0, 0] and not row.get("observed_models"))
            if row["status"] == "completed":
                require(counters == [row[key] for key in ("tokens", "turns", "tool_calls")])
                if row["arm"] in ARMS:
                    require(usage == "observed_not_total_billed" and bool(row.get("observed_models")))
        if row["status"] != "completed":
            require(
                not {
                    "trusted",
                    "weak",
                    "cases",
                    "semantic_passes",
                    "timeouts",
                    "native_result_sha256",
                    "submission_sha256",
                }.intersection(row)
            )
            continue
        require(mode != "plan")
        case_count = integer(row["cases"], 1, 1000000)
        require(denominators.setdefault(row["task_id"], case_count) == case_count)
        semantic = integer(row["semantic_passes"], 0, case_count)
        timeouts = integer(row["timeouts"], 0, case_count)
        trusted, weak = (integer(row[key], 0, 1) for key in ("trusted", "weak"))
        require(trusted == int(semantic == case_count) and trusted <= weak)
        require(semantic + timeouts <= case_count and not (weak and timeouts))
        for key in ("turns", "tool_calls", "tokens"):
            integer(row[key], int(row["arm"] in ARMS and key != "tool_calls"), 1000000000)
        hash_value(row["submission_sha256"])
        hashes.append(hash_value(row["native_result_sha256"]))
    completed = sum(row["status"] == "completed" for row in rows)
    integer(report["completed"], completed, completed)
    integer(report["incomplete"], len(rows) - completed, len(rows) - completed)
    healthy = controls_healthy([row for row in rows if row["arm"] in CONTROLS])
    require(type(report["controls_healthy"]) is bool and report["controls_healthy"] == healthy)
    status = "planned" if mode == "plan" else "completed" if completed == len(rows) and healthy else "incomplete"
    require(report["status"] == status)
    reason = report["reason"]
    require(
        reason
        in (
            "none",
            "in-progress",
            "interrupted",
            "missing-key",
            "unhealthy-controls",
            "invalid-native-evidence",
            "infrastructure",
            "deadline",
        )
    )
    require((reason == "none") == (status in ("completed", "planned")))
    if reason == "missing-key":
        require(mode == "live" and all(row["status"] == "pending" for row in rows))
    elif reason == "unhealthy-controls":
        require(not healthy)
    elif reason == "invalid-native-evidence":
        require(any(row["status"] == "incomplete" for row in rows))
    require(canonical(report["metrics"]) == canonical(metrics(rows)))
    components = {key: hash_value(value) for key, value in report["bindings"].items() if key != "comparison_digest"}
    require({f"{name}_digest" for name in ("runtime", "model", "prompt", "task", "measurement")} <= components.keys())
    require(all(re.fullmatch(r"[a-z_]+_digest", key) for key in components))
    digest = sha(canonical(components))
    require(report["bindings"]["comparison_digest"] == report["comparison_digest"] == digest)
    runtime = report["runtime"]
    require({"harbor", "python", "system", "machine"} <= runtime.keys())
    require(
        runtime["harbor"] is None
        or (
            isinstance(runtime["harbor"], str)
            and re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", runtime["harbor"])
        )
    )
    require(runtime["system"] in ("linux", "darwin", "other"))
    require(runtime["machine"] in ("x86_64", "aarch64", "arm64", "other"))
    require(isinstance(runtime["python"], list) and len(runtime["python"]) == 3)
    for value in runtime["python"]:
        integer(value, 0, 10000)
    docker = runtime.get("docker")
    if docker is not None:
        hash_value(docker["image_id"])
        for key in ("server_version", "api_version"):
            require(isinstance(docker[key], str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", docker[key]))
        for key in ("server_os", "image_os"):
            require(docker[key] in ("linux", "windows"))
        for key in ("server_arch", "image_arch"):
            require(docker[key] in ("amd64", "arm64", "x86_64", "aarch64"))
    run_id, attempt = (integer(report["ci"][key], 0, 10**18 - 1) for key in ("run_id", "run_attempt"))
    require(bool(run_id) == bool(attempt))
    return hashes, (run_id, attempt), sorted(denominators.items())


def provider_identity(rows):
    observations, associated = set(), []
    for row in rows:
        models = row.get("observed_models", [])
        require(isinstance(models, list) and len(models) <= 1000)
        unique = set()
        for model in models:
            require(set(model) == {"model_sha256", "revision_sha256"})
            identity = (
                hash_value(model["model_sha256"]),
                hash_value(model["revision_sha256"]) if model["revision_sha256"] is not None else None,
            )
            require(identity not in unique)
            unique.add(identity)
        if row["arm"] in ARMS:
            observations.update(unique or {(None, None)})
            associated.append([row["cell_id"], sorted(canonical(item).decode() for item in unique or {(None, None)})])
    state = (
        "unknown"
        if not observations or (None, None) in observations
        else ("unknown-revision" if any(revision is None for _, revision in observations) else "observed-revision")
    )
    return associated, state


def compare(reports):
    require(len(reports) >= 2)
    groups, native_seen, ci_seen, summaries_seen = {}, set(), set(), set()
    for index, report in enumerate(reports, 1):
        hashes, ci, denominators = validate(report)
        require(len(set(hashes)) == len(hashes) and not native_seen.intersection(hashes))
        require(not ci[0] or ci not in ci_seen)
        fingerprint = canonical(
            [
                report["comparison_digest"],
                report["mode"],
                report["expected"],
                ci,
                [(row["cell_id"], row["status"]) for row in report["cells"]],
                hashes,
            ]
        )
        require(fingerprint not in summaries_seen)
        native_seen.update(hashes)
        ci_seen.add(ci)
        summaries_seen.add(fingerprint)
        observed, identity_state = provider_identity(report["cells"])
        if "observed_models" in report:
            inventory = {canonical(model) for row in report["cells"] for model in row.get("observed_models", [])}
            require(
                canonical(report["observed_models"]) == canonical([json.loads(value) for value in sorted(inventory)])
            )
        key = canonical(
            [report["comparison_digest"], report["mode"], report["expected"], report["runtime"], denominators, observed]
        )
        series = groups.setdefault(
            key,
            {
                "series_id": len(groups) + 1,
                "series_fingerprint": sha(key),
                "mode": report["mode"],
                "expected": report["expected"],
                "provider_identity": identity_state,
                "measurements": [],
            },
        )
        arms = []
        for arm in (*CONTROLS, *ARMS):
            group = [row for row in report["cells"] if row["arm"] == arm]
            completed = sum(row["status"] == "completed" for row in group)
            arms.append(
                {"arm": arm, "expected": len(group), "completed": completed, "incomplete": len(group) - completed}
            )
        series["measurements"].append(
            {
                "arms": arms,
                "input_index": index,
                "run_id": report["ci"]["run_id"],
                "run_attempt": report["ci"]["run_attempt"],
                "status": report["status"],
                "completed": report["completed"],
                "incomplete": report["incomplete"],
                "controls_healthy": report["controls_healthy"],
                "metrics": metrics(report["cells"]),
            }
        )
    for series in groups.values():
        ci_order = all(row["run_id"] for row in series["measurements"])
        series["ordering"] = "ci-run-attempt" if ci_order else "input-order"
        if ci_order:
            series["measurements"].sort(key=lambda row: (row["run_id"], row["run_attempt"]))
    return {
        "schema": "anti-cheat-history-v1",
        "evidence": "summary-only",
        "batch_count": len(reports),
        "series_count": len(groups),
        "series": list(groups.values()),
    }


def main():
    try:
        result = compare([load_json(Path(path)) for path in sys.argv[1:]])
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        print(json.dumps({"schema": "anti-cheat-history-v1", "status": "rejected", "reason": "invalid-summary"}))
        return 2
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
