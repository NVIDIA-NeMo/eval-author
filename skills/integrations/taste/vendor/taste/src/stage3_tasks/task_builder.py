# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, field

from src.backends.contracts import NativeTask, ValidationResult


@dataclass
class ValidationMetadata:
    success: bool = False
    solver_retries: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        result = {"validation_success": self.success, "solver_retries": self.solver_retries}
        if self.error is not None:
            result["validation_error"] = self.error
        return result


@dataclass
class GeneratedTask:
    task: NativeTask
    validation: ValidationMetadata = field(default_factory=ValidationMetadata)

    @classmethod
    def create(
        cls, task_dict, db_entities=None, domain=None, *, domain_config=None, validation_result: ValidationResult
    ):
        if domain_config is None:
            raise ValueError("GeneratedTask requires prepared domain_config")
        task = NativeTask(
            {
                "id": "generated_unassigned",
                "candidate": task_dict,
                "fixture": db_entities or {},
                "native_task": validation_result.native_task,
            }
        )
        return cls(task)

    def to_dict(self):
        result = self.task.to_dict()
        return result
