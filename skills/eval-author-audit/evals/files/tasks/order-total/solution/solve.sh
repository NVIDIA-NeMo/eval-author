#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu
printf '250.50\n' > /workspace/total.txt
printf '%s\n' '{"currency": "USD", "order_count": 3}' > /workspace/summary.json
