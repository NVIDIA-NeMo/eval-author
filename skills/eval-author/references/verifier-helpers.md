<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Trusted verifier helpers

Use this boundary when a grader delegates evaluation to its own helper. A missing
file, syntax error, failed import, malformed response or unexpected helper exit
must propagate as a verifier error. A reserved error exit alone is insufficient:
startup failures can happen before the helper installs its error handler.
Compilation catches syntax errors but does not establish import/runtime health.

Keep submission execution separate. A submitted program may legitimately fail,
time out or return invalid output. The trusted helper handles that bounded
submission invocation and reports its outcome explicitly. A timeout of the outer
helper is an infrastructure failure; give the helper enough time to enforce its
own shorter submission limit and report the result. Never run the submitted
program directly through a transport that interprets every nonzero exit as a
verifier failure.

Existing provider-native protocols remain valid. For a small Python verifier,
the optional [helper transport](../scripts/verifier_helper.py) provides a strict
JSON envelope and bounded subprocess execution. Copy it into the verifier-owned
image alongside your driver; do not depend on an installed skill at task runtime.
Helpers write exactly one of these envelopes to stdout and diagnostics to stderr:

```json
{"status": "result", "value": {"observed": 7}}
```

```json
{"status": "submission_error", "reason": "submitted program timed out"}
```

The transport validates the envelope, not the answer or the helper's attribution.
Its `result` value may be wrong and still be a healthy evaluation. Only a trusted
helper may declare `submission_error`; this is not proof of hostile-code isolation.
Use the native sandbox to isolate untrusted code, limit its resources and clean
up descendants. On POSIX the transport kills its helper process group; elsewhere
its cleanup guarantee covers only the immediate helper process.

```python
from verifier_helper import run_helper

response = run_helper(
    [python, "/tests/driver.py", submission_path],
    timeout_seconds=10,
    max_output_bytes=65536,
)
if response["status"] == "submission_error":
    correct = False
else:
    correct = response["value"] == independently_computed_expected
```

Do not catch `VerifierHelperError` and convert it into `False` or reward zero.
Let the verifier's outer error handler remove stale reward, record failed health
and propagate through the native provider's error path. Emit healthy health and
reward only after all checks finish. The transport limits combined stdout/stderr,
rejects nonzero exits even with valid stdout, and rejects malformed JSON including
duplicate keys, nonfinite values and unexpected fields. It does not log complete
commands or helper output.

Package helpers for their actual runtime identity. A root-readable source file
may be unreadable after a UID change: Docker copies preserve file modes. Set
explicit read/execute permissions on the necessary helpers and traverse permissions
on their directories, while keeping grading inputs and reward output protected
from submission writes. Give reference outputs the access the evaluator needs;
preserve any file modes that are themselves part of the task's requirements.
Do not solve access failures by running untrusted submissions as root.

Exercise helper startup and imports under the same UID, working directory and
entry point used for grading. A parent-side import or syntax check does not cover
that boundary. For verifier self-tests, report the names of failed checks without
printing secrets. Parse tool output using its documented format, including labels
and prefixes; a self-test must accept its own independently known-valid fixture
and reject a meaningful invalid one before it can diagnose submissions.

Before acceptance, exercise ordinary correct and incorrect submissions. When
helper handling is uncertain, also test separate diagnostic copies with a missing
helper, syntax error, missing startup import, and malformed output with both zero
and nonzero exits. Bind each copy to the original task and exact mutation. Expect
native verifier errors with no valid reward for these helper faults; a healthy
negative is a fault-classification defect. Keep diagnostics separate from scored
controls and original receipts. Apply the same fault matrix to both comparison
arms; source review or an ordinary solver pass cannot establish broader health.
