# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import sys

values = sorted(set(json.load(sys.stdin)))
print(json.dumps([bool(value) if value in (0, 1) else value for value in values]))
