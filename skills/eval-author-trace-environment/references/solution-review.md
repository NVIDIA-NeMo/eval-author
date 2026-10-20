<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Optional solution review

Construction controls establish specific behaviors, but do not establish that
new solutions receive correct scores. Use this experimental diagnostic when
reviewing that gap. Keep every input, judgment, transcript and report in the
ignored task workspace. It supports Harbor workspaces with a valid construction contract.
It does not launch agents, call models, replace task proof, or change readiness.
Existing `check`, `finalize` and export do **not** incorporate these findings.
Investigate discrepancies before handoff; never describe the report as task
acceptance, measured difficulty, or proof of reviewer independence.

## Capture the submission used for grading

Before freezing the task, copy `scripts/review_runtime.py` unchanged into
`task/tests/review_runtime.py`. In the isolated verifier, use its
`grade_submission` function for submission-dependent checks:

```python
from pathlib import Path
from review_runtime import grade_submission


def assess(submission):
    # Grade captured bytes, never reopen solver paths here.
    return {"answer-correct": submission["answer"] == b"correct\n"}


grade_submission({"answer": Path("/workspace/output.txt")}, assess)
```

Declare the normal Harbor artifact handoff for each solver output. The wrapper
captures at most 32 regular files and 2 MiB total, passes immutable bytes to the
callback, and writes ordinary tab-separated `results`, binary `reward.txt`,
submission snapshots and a completion receipt under `/logs/verifier`. Use a
fresh verifier log directory. A missing source is passed as `None`; handle it
explicitly so ordinary NOP controls still receive a failing score. The receipt
records captured absence as `null`; this is submission evidence, not an automatic
pass or failure. The snapshot directory must exist and contain exactly the
non-null entries. Missing expected snapshots or contradictory files remain
unresolved. Symbolic links produce a completed failed capture. Callback failures
and invalid callback checks do not produce a completed receipt.

Review the callback and artifact handoff. It must grade those captured bytes,
cover exactly the construction manifest's check IDs, and avoid solver-controlled
verifier code. Matching producer hashes and receipts establish retained-byte
consistency within the trusted verifier, not cryptographic attestation or proof
that callback semantics match the instruction. Adding the wrapper changes the
task digest: rerun existing construction proof rather than reusing old jobs.

## Freeze the complete cohort before running jobs

Author `private/review-plan.json`, using workspace-relative canonical paths:

```json
{
  "author_id": "task-author",
  "author_context_id": "author-session",
  "attempts": [
    {
      "id": "solution-one",
      "purpose": "solution",
      "job_dir": "private/review-jobs/solution-one",
      "agent": "configured-agent",
      "model_name": "configured-model",
      "context_id": "fresh-solver-session",
      "exposure": "public_only",
      "prompt": "private/prompts/solution-one.txt",
      "transcript": "agent/trajectory.json"
    }
  ]
}
```

Use the actual Harbor agent name or import path and model identity (`null` for a
scripted control). Retain the prompt file before recording. The transcript path
is relative to the eventual trial directory. Plan up to 32 distinct attempts;
use separate contexts, unique future job directories and a fixed budget. A useful
small starting cohort is four solution attempts and one adversarial attempt,
when authorized. `purpose` is `solution` or `adversarial`; solver `exposure` is
`public_only`, `verifier_visible` or `unknown`. Built-in Oracle is rejected.
Custom-agent behavior, prompt delivery and exposure still require review.

```bash
python scripts/trace_environment.py record-review-inputs \
  --task-dir <workspace> --plan private/review-plan.json
```

Retain the returned cohort path and digest. The receipt binds the complete plan,
prompts, task tree, provider checksum, construction manifest and capture helper.
Every job directory must be absent at recording. Run the declared jobs using the
installed compatible Harbor runtime, retaining their provider result, verifier
logs and agent transcript. Every planned attempt remains in the denominator,
including jobs never started. Do not edit receipts or discard failed attempts.
An additional experiment requires a new plan, fresh jobs and a new cohort.

## Review correctness against the public requirements

Use a reviewer with a separate context, initially showing the public contract,
submission snapshots and solver transcript without the verifier score when
possible. Record actual exposure; different identifiers alone do not establish
independence. Retain `private/solution-assessments.json`:

```json
{
  "schema": "nemo.eval_author.solution_assessments.v1",
  "cohort_sha256": "<digest returned by record-review-inputs>",
  "reviewer_id": "solution-reviewer",
  "context_id": "fresh-review-session",
  "exposure": "submission_and_contract",
  "judgments": [
    {
      "attempt_id": "solution-one",
      "requirements": [
        {
          "id": "<construction requirement ID>",
          "decision": "met",
          "rationale": "Explain how the retained submission satisfies this requirement.",
          "citations": [
            "task/instruction.md",
            "private/review-jobs/solution-one/<trial>/verifier/review-submission/answer"
          ]
        }
      ]
    }
  ]
}
```

Review every mapped requirement exactly once: `met`, `unmet` or `uncertain`.
Each judgment must cite the public instruction and that trial's captured
submission or transcript. Reviewer exposure is `submission_and_contract`,
`verdict_visible` or `unknown`. Never claim blindness from this declaration.
Omit an unavailable judgment; its attempt remains unresolved. Rationales and
citations are review attestations, not automatically verified semantics.

For a captured absence, cite the exact null entry, for example
`private/review-jobs/solution-one/<trial>/verifier/review-submission.json#/submissions/support`.
Only this locator shape for an actual null entry is supported, not arbitrary
JSON pointers. The diagnostic binds the complete receipt hash and rejects any
snapshot contradicting the absence. Give reviewers only an allowlisted projection
of submission IDs, presence/absence and these locators, alongside present
snapshots. Do not expose the raw receipt: it also contains checks and reward.
An all-absent submission can be reviewed using these locators; never fabricate
an empty file to stand in for absence. Missing required output may be judged
`unmet`, while missing optional output may be acceptable or remain `uncertain`
under the public contract. Absence alone does not decide correctness.

```bash
python scripts/trace_environment.py review-solutions \
  --task-dir <workspace> \
  --cohort private/solution-review/cohorts/<digest>.json \
  --assessments private/solution-assessments.json
```

## Interpret the diagnostic

| Reviewed submission | Consistent verifier score | Outcome |
|---|---|---|
| All requirements met | 1 | Legitimate pass |
| All requirements met | 0 | Reviewed false rejection |
| At least one requirement unmet | 1 | Reviewed false acceptance |
| At least one requirement unmet | 0 | Legitimate failure |
| Uncertain, with none unmet | Either | Unresolved assessment |

A mismatch requires matching task/agent identity, captured-submission receipt,
check inventory, check rows, binary reward and retained transcript. Missing or
conflicting evidence, provider exceptions, nonbinary rewards and missing
reviews stay unresolved. Task/identity mismatch or malformed review input is a
contract error. Adversarial findings are reviewed score discrepancies, never
confirmed exploits without separate reproduction evidence. An all-pass or
all-fail cohort is a difficulty observation, not a task-invalidity rule.

Output always says `diagnostic_only` and `declared_separation_only`. Status is
`findings_present`, `incomplete`, or `no_findings_in_reviewed_cohort`. Findings
can coexist with unresolved attempts: inspect both counts. A successful command
means the diagnostic was written, not that the task passed. Repeating review
rereads source evidence and writes an immutable content-addressed private
report; changing evidence or judgments produces a new revision. Reports bind
full evidence hashes and preserve exposure declarations. CLI summaries omit
raw judgments, transcripts and identities. Reports are not exported.

### Runtime and evidence boundaries

Run exactly one trial per declared job directory: disable Harbor retries and use
`n_attempts = 1`. Multiple trials are unresolved (`missing_or_ambiguous_trial`).
An unreadable or interrupted result leaves that attempt unresolved without
removing it from the denominator. Binding mismatches still reject the cohort.

The current capture producer emits submission v2. Invalid symlinks, non-regular
or oversized outputs and invalid parent components are recorded as null with an
`invalid` reason. Combined overflow invalidates all captures. The callback receives
null for these inputs; the wrapper forces every declared check to FAIL and reward 0
when any input is invalid, even if the callback accepts null. Ordinary absent
optional files remain null without an invalid reason. NOP/negative controls with
malformed outputs therefore complete with reward 0 rather than a verifier crash.
Permission errors and callback errors remain infrastructure failures. Review packets
may project invalid reasons without exposing receipt verdicts or check outcomes.
Old v1 capture tasks need a fresh producer, task binding, cohort and execution.

Verifier output directories follow the process umask. Keep the host job ancestor
private and arrange readable ownership/ACLs when collecting from a different
container UID. Rootful Linux bind mounts are not guaranteed readable by the host
user; `submission_evidence_unreadable` distinguishes permission failure from
inconsistent submission binding. No cross-UID portability claim is made.

Receipts detect edits and bind retained evidence; they do not authenticate authors,
execution times, or enforce the denominator against an author who moves/deletes
jobs or cohorts. Reports inventory all discoverable cohorts for the same binding
(up to 256 entries), including unreadable/limited-discovery warnings. Review this
inventory for omitted experiments. Missing inventory entries are not evidence that
no other experiment ran. There is no timestamp-based anti-reuse guarantee.
