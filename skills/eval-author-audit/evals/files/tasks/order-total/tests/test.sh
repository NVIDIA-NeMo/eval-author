#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu
mkdir -p /logs/verifier
python - <<'CHECK'
import json
from pathlib import Path
ok = False
try:
    total = Path('/workspace/total.txt').read_text().strip()
    summary = json.loads(Path('/workspace/summary.json').read_text())
    ok = total == '250.50' and summary == {'currency': 'USD', 'order_count': 3}
except (OSError, ValueError):
    pass
Path('/logs/verifier/reward.txt').write_text('1.0' if ok else '0.0')
CHECK
