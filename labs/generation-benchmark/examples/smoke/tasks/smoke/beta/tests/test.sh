#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

mkdir -p /logs/verifier
if [ -f /app/answer ] && [ "$(cat /app/answer)" = "ready" ]; then echo 1; else echo 0; fi > /logs/verifier/reward.txt
