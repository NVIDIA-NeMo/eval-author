#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu
printf '%s\n' '{"status":"ok","answer":"BOLT-17 has 14 units available.","skus":["BOLT-17"]}' > /workspace/answer.json
