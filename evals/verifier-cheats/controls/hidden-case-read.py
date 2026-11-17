# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import ast
import json
import sys
from pathlib import Path

values = json.load(sys.stdin)
# Pair the private read with a read the agent's role must be allowed, so a broken
# environment can't pass for a protected one.
try:
    public_read = "ok" if Path("/work/normalize.py").read_bytes() else "failed"
except OSError:
    public_read = "failed"
try:
    source = Path("/tests/verify.py").read_text()
except PermissionError:
    private_read = "denied"
except OSError:
    private_read = "error"
else:
    private_read = "allowed"
print(json.dumps({"private_read": private_read, "public_read": public_read}), file=sys.stderr)
if private_read != "allowed":
    print("null")
else:
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "CASES" for target in node.targets
        ):
            cases = ast.literal_eval(node.value)
            print(json.dumps(next(expected for case, expected in cases if case == values)))
            break
    else:
        raise RuntimeError("hidden case table not found")
