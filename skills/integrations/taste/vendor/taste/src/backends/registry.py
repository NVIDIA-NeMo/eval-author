# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from importlib import import_module

from src.backends.contracts import IncompatibleBackend


def load_task_service(reference: str | None, *, config):
    if not reference or ":" not in reference:
        raise IncompatibleBackend(
            "Stage 3 requires task_service='module:factory' in the prepared bundle; "
            "stages 1 and 2 do not require a task runtime"
        )
    module_name, factory_name = reference.split(":", 1)
    if not module_name or not factory_name:
        raise IncompatibleBackend("task_service must name an importable module:factory")
    factory = getattr(import_module(module_name), factory_name)
    if not callable(factory):
        raise IncompatibleBackend(f"Task service factory is not callable: {reference}")
    return factory(config=config)
