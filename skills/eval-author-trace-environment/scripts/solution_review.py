# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Private, optional solution/verdict diagnostics; never an admission gate."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import construction as contract
from qualification_runtime import digest
from review_runtime import SCHEMA as SUBMISSION_SCHEMA
from review_runtime import read_regular, sha

COHORT_SCHEMA = "nemo.eval_author.solution_review_cohort.v1"
ASSESSMENT_SCHEMA = "nemo.eval_author.solution_assessments.v1"
REPORT_SCHEMA = "nemo.eval_author.solution_review.v1"
ATTEMPT_KEYS = "id purpose job_dir agent model_name context_id exposure prompt transcript"
require = contract.require
fields = contract.fields


def private_path(root: Path, relative: str) -> Path:
    path = contract.secure_path(root, relative)
    require(relative == path.relative_to(root).as_posix(), "evidence paths must be canonical")
    require(
        relative.startswith("private/") and "probes" not in path.relative_to(root).parts, "private evidence required"
    )
    return path


def persist(root: Path, category: str, value: dict) -> str:
    relative = f"private/solution-review/{category}/{digest(value)}.json"
    path = private_path(root, relative)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    if path.exists():
        require(read_regular(path) == data, "existing review artifact changed")
    else:
        with path.open("xb") as stream:
            path.chmod(0o600)
            stream.write(data)
    return relative


def read(root: Path, relative: str, evidence: dict) -> bytes:
    path = contract.secure_path(root, relative)
    data = read_regular(path)
    evidence[relative] = sha(data)
    return data


def load(root: Path, relative: str, evidence: dict) -> object:
    # Reuse duplicate-key/nonfinite rejection, and bind exactly the parsed bytes.
    data = read(root, relative, evidence)
    value = contract.load(root, relative)
    require(sha(read_regular(root / relative)) == sha(data), "evidence changed while reading")
    return value


def validate_plan(root: Path, value: object) -> dict:
    plan = fields(value, "author_id author_context_id attempts", "review plan")
    require(all(contract.text(plan[key]) for key in ("author_id", "author_context_id")), "author identity required")
    attempts = plan["attempts"]
    require(isinstance(attempts, list) and 1 <= len(attempts) <= 32, "expected 1..32 planned attempts")
    ids, jobs, contexts = set(), set(), {plan["author_context_id"]}
    for attempt in attempts:
        attempt = fields(attempt, ATTEMPT_KEYS, "planned attempt")
        require(contract.identity(attempt["id"]) and attempt["id"] not in ids, "duplicate or invalid attempt ID")
        ids.add(attempt["id"])
        require(attempt["purpose"] in ("solution", "adversarial"), "unknown attempt purpose")
        require(attempt["exposure"] in ("public_only", "verifier_visible", "unknown"), "declare solver exposure")
        require(
            all(contract.text(attempt[key]) for key in ("agent", "context_id", "transcript")),
            "attempt identity missing",
        )
        require(
            attempt["agent"] not in ("oracle", "harbor.agents.oracle:OracleAgent"),
            "Oracle is not an independent solution",
        )
        require(attempt["model_name"] is None or contract.text(attempt["model_name"]), "invalid model identity")
        require(
            attempt["context_id"] not in contexts, "solver contexts must be distinct from author and other attempts"
        )
        contexts.add(attempt["context_id"])
        job = private_path(root, attempt["job_dir"])
        require(job not in jobs, "duplicate job directory")
        require(not any(job in other.parents or other in job.parents for other in jobs), "job directories overlap")
        jobs.add(job)
        private_path(root, attempt["prompt"])
        transcript = contract.secure_path(root, attempt["transcript"])
        require(
            attempt["transcript"].startswith("agent/") and transcript.suffix in (".json", ".jsonl", ".txt"),
            "transcript must name a retained agent log",
        )
    return plan


def record(root: Path, plan_path: str, binding: dict) -> dict:
    private_path(root, plan_path)
    evidence: dict[str, str] = {}
    plan = validate_plan(root, load(root, plan_path, evidence))
    for attempt in plan["attempts"]:
        require(not private_path(root, attempt["job_dir"]).exists(), "record the entire cohort before any job exists")
        read(root, attempt["prompt"], evidence)
    producer = read_regular(Path(__file__).with_name("review_runtime.py"))
    require(
        read_regular(root / "task/tests/review_runtime.py") == producer, "copy the bundled review producer into tests"
    )
    receipt = {
        "schema": COHORT_SCHEMA,
        "plan_path": plan_path,
        "plan": plan,
        "binding": binding,
        "inputs": evidence,
        "producer_sha256": sha(producer),
    }
    relative = persist(root, "cohorts", receipt)
    return {
        "diagnostic_only": True,
        "attempts": len(plan["attempts"]),
        "cohort": relative,
        "cohort_sha256": digest(receipt),
    }


def assessment_map(value: object, cohort: dict, requirements: list[str]) -> dict:
    assessment = fields(value, "schema cohort_sha256 reviewer_id context_id exposure judgments", "assessments")
    require(
        assessment["schema"] == ASSESSMENT_SCHEMA and assessment["cohort_sha256"] == digest(cohort),
        "assessment cohort mismatch",
    )
    plan = cohort["plan"]
    require(
        contract.text(assessment["reviewer_id"]) and assessment["reviewer_id"] != plan["author_id"],
        "distinct reviewer required",
    )
    require(
        contract.text(assessment["context_id"])
        and assessment["context_id"] not in {plan["author_context_id"], *(a["context_id"] for a in plan["attempts"])},
        "distinct reviewer context required",
    )
    require(
        assessment["exposure"] in ("submission_and_contract", "verdict_visible", "unknown"), "declare reviewer exposure"
    )
    judgments = assessment["judgments"]
    require(isinstance(judgments, list) and len(judgments) <= len(plan["attempts"]), "bounded judgments required")
    result = {}
    known = {a["id"] for a in plan["attempts"]}
    for judgment in judgments:
        judgment = fields(judgment, "attempt_id requirements", "judgment")
        key = judgment["attempt_id"]
        require(isinstance(key, str) and key in known and key not in result, "unknown or duplicate judgment")
        rows = judgment["requirements"]
        require(isinstance(rows, list) and len(rows) == len(requirements), "assess every mapped requirement")
        seen = set()
        for row in rows:
            row = fields(row, "id decision rationale citations", "requirement judgment")
            require(
                isinstance(row["id"], str) and row["id"] in requirements and row["id"] not in seen,
                "unknown or repeated requirement",
            )
            seen.add(row["id"])
            require(
                row["decision"] in ("met", "unmet", "uncertain") and contract.text(row["rationale"]),
                "explain requirement decision",
            )
            require(
                isinstance(row["citations"], list)
                and 2 <= len(row["citations"]) <= 16
                and all(isinstance(c, str) for c in row["citations"]),
                "cite the public contract and trial evidence",
            )
        result[key] = rows
    return result


def review_attempt(root: Path, attempt: dict, rows: list | None, cohort: dict, evidence: dict) -> str:
    job = private_path(root, attempt["job_dir"])
    if not job.exists():
        return "missing_job"
    trials = []
    with os.scandir(job) as entries:
        for index, entry in enumerate(entries):
            if index >= 256:
                return "trial_discovery_limit"
            path = Path(entry.path)
            if entry.is_symlink():
                return "unsafe_trial_path"
            if entry.is_dir(follow_symlinks=False) and (path / "result.json").exists():
                trials.append(path / "result.json")
                if len(trials) == 2:
                    break
    if len(trials) != 1:
        return "missing_or_ambiguous_trial"
    trial = trials[0].parent.relative_to(root).as_posix()
    result = load(root, f"{trial}/result.json", evidence)
    if not isinstance(result, dict):
        raise contract.ConstructionError("trial result must be an object")
    require(result.get("task_checksum") == cohort["binding"]["task_checksum"], "trial task differs from cohort")
    config = result.get("config")
    if not isinstance(config, dict) or not isinstance(config.get("agent"), dict):
        raise contract.ConstructionError("trial agent missing")
    agent = config["agent"]
    identity = agent.get("import_path") or agent.get("name")
    require(not (agent.get("import_path") and agent.get("name")), "ambiguous trial agent")
    require(
        identity == attempt["agent"] and agent.get("model_name") == attempt["model_name"],
        "trial agent differs from cohort",
    )
    if result.get("exception_info") is not None:
        return "infrastructure_failure"
    if result.get("verifier_environment_mode") != "separate":
        return "verifier_isolation_unverified"
    verifier = result.get("verifier_result")
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    if type(reward) not in (int, float) or reward not in (0, 1):
        return "missing_or_nonbinary_reward"
    prefix = f"{trial}/verifier"
    try:
        receipt = fields(
            load(root, f"{prefix}/review-submission.json", evidence),
            "schema producer_sha256 submissions checks reward",
            "submission receipt",
        )
        require(
            receipt["schema"] == SUBMISSION_SCHEMA and receipt["producer_sha256"] == cohort["producer_sha256"],
            "unbound producer",
        )
        submissions, checks = receipt["submissions"], receipt["checks"]
        require(
            isinstance(submissions, dict)
            and 1 <= len(submissions) <= 32
            and all(contract.identity(k) for k in submissions),
            "invalid submission inventory",
        )
        require(
            isinstance(checks, dict)
            and set(checks) == set(cohort["binding"]["checks"])
            and all(type(v) is bool for v in checks.values()),
            "invalid check inventory",
        )
        require(
            type(receipt["reward"]) is int and receipt["reward"] == reward == int(all(checks.values())),
            "conflicting verdicts",
        )
        expected = "".join(f"{key}\t{'PASS' if value else 'FAIL'}\n" for key, value in sorted(checks.items()))
        require(read(root, f"{prefix}/results", evidence) == expected.encode(), "conflicting check rows")
        require(
            read(root, f"{prefix}/reward.txt", evidence).strip() == str(int(reward)).encode(), "conflicting reward file"
        )
        total = 0
        for key, checksum in submissions.items():
            data = read(root, f"{prefix}/review-submission/{key}", evidence)
            total += len(data)
            require(sha(data) == checksum and total <= contract.MAX_BYTES, "snapshot changed or oversized")
    except (ValueError, OSError):
        return "submission_binding_unverified"
    try:
        read(root, f"{trial}/{attempt['transcript']}", evidence)
    except (ValueError, OSError):
        return "missing_transcript"
    if rows is None:
        return "missing_assessment"
    for row in rows:
        citations = row["citations"]
        allowed = {f"{prefix}/review-submission/{key}" for key in submissions} | {f"{trial}/{attempt['transcript']}"}
        require(
            "task/instruction.md" in citations
            and bool(set(citations) & allowed)
            and set(citations) <= allowed | {"task/instruction.md"},
            "citation must bind this trial and public contract",
        )
        for citation in citations:
            read(root, citation, evidence)
    decisions = {row["decision"] for row in rows}
    if "unmet" in decisions:
        return "false_accept" if reward == 1 else "legitimate_fail"
    if "uncertain" in decisions:
        return "uncertain_assessment"
    return "legitimate_pass" if reward == 1 else "false_reject"


def review(root: Path, cohort_path: str, assessment_path: str, binding: dict) -> dict:
    for path in (cohort_path, assessment_path):
        private_path(root, path)
    evidence: dict[str, str] = {}
    cohort = fields(
        load(root, cohort_path, evidence), "schema plan plan_path binding inputs producer_sha256", "cohort receipt"
    )
    require(cohort["schema"] == COHORT_SCHEMA and cohort["binding"] == binding, "stale or invalid review cohort")
    require(cohort_path == f"private/solution-review/cohorts/{digest(cohort)}.json", "cohort receipt was edited")
    validate_plan(root, cohort["plan"])
    private_path(root, cohort["plan_path"])
    require(load(root, cohort["plan_path"], evidence) == cohort["plan"], "retained plan differs from cohort")
    require(
        set(cohort["inputs"]) == {cohort["plan_path"], *(a["prompt"] for a in cohort["plan"]["attempts"])},
        "cohort inputs incomplete",
    )
    require(isinstance(cohort["inputs"], dict), "invalid cohort inputs")
    for path, checksum in cohort["inputs"].items():
        private_path(root, path)
        require(sha(read(root, path, evidence)) == checksum, "pre-run inputs changed")
    require(
        cohort["producer_sha256"]
        == sha(read_regular(Path(__file__).with_name("review_runtime.py")))
        == sha(read_regular(root / "task/tests/review_runtime.py")),
        "review producer changed",
    )
    assessment = load(root, assessment_path, evidence)
    judgments = assessment_map(assessment, cohort, binding["requirements"])
    outcomes = []
    for attempt in cohort["plan"]["attempts"]:
        outcome = review_attempt(root, attempt, judgments.get(attempt["id"]), cohort, evidence)
        outcomes.append({"attempt_id": attempt["id"], "purpose": attempt["purpose"], "outcome": outcome})
    counts = dict(sorted(Counter(o["outcome"] for o in outcomes).items()))
    resolved = {"false_accept", "false_reject", "legitimate_pass", "legitimate_fail"}
    incomplete = sum(n for key, n in counts.items() if key not in resolved)
    findings = counts.get("false_accept", 0) + counts.get("false_reject", 0)
    status = "findings_present" if findings else "incomplete" if incomplete else "no_findings_in_reviewed_cohort"
    report = {
        "schema": REPORT_SCHEMA,
        "diagnostic_only": True,
        "independence": "declared_separation_only",
        "status": status,
        "attempts": len(outcomes),
        "unresolved": incomplete,
        "counts": counts,
        "cohort_sha256": digest(cohort),
        "outcomes": outcomes,
        "solver_exposure": dict(Counter(a["exposure"] for a in cohort["plan"]["attempts"])),
        "reviewer_exposure": assessment["exposure"] if isinstance(assessment, dict) else "unknown",
        "evidence": evidence,
    }
    relative = persist(root, "reports", report)
    return {
        key: report[key] for key in ("diagnostic_only", "independence", "status", "attempts", "unresolved", "counts")
    } | {"report": relative}
