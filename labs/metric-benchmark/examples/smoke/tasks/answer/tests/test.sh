#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

mkdir -p /logs/verifier
if test -f /app/answer; then echo 1; else echo 0; fi > /logs/verifier/reward.txt
