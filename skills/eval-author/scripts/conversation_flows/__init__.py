# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Static registrations for implemented conversation flows."""

from collections.abc import Callable

from . import first_eval, onboarding

FLOWS = (onboarding, first_eval)
HANDLERS: dict[tuple[str | None, str], Callable[[dict], str]] = {}
PREVIEWS: set[str] = set()
for flow in FLOWS:
    PREVIEWS.update(flow.PREVIEWS)
    for journey in flow.JOURNEYS:
        for stage in flow.STAGES:
            key = (journey, stage)
            if key in HANDLERS:
                raise ValueError(f"Duplicate conversation handler for journey {journey!r}, stage {stage!r}")
            HANDLERS[key] = flow.render
STAGES = {stage for _, stage in HANDLERS}
