#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu
exec /usr/local/bin/python -I -S -B /tests/grade.py --state /export/state.json --reward-dir /logs/verifier
