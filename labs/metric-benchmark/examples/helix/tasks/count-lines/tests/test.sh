#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu
mkdir -p /logs/verifier
python3 - <<'CHECK'
from pathlib import Path
answer = Path('/app/answer.txt')
valid = answer.is_file() and answer.read_bytes() == b'3\n'
Path('/logs/verifier/reward.txt').write_text('1\n' if valid else '0\n')
CHECK
