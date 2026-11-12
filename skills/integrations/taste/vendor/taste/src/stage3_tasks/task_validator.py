# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import json
import uuid
from pathlib import Path

from src.backends.contracts import InfrastructureError, ValidationResult
from src.common.partial_coverage_hints import ActionHints


class TaskValidator:
    def __init__(
        self,
        domain=None,
        domain_config=None,
        *,
        gt_llm=None,
        partial_coverage_p=0.33,
        gt_coverage_shuffle=True,
        write_only=False,
    ):
        if domain_config is None:
            raise ValueError("TaskValidator requires prepared inputs and an EA task service")
        self.config = domain_config
        self.implementation = self.config.service()
        self.p, self.shuffle = partial_coverage_p, gt_coverage_shuffle
        self.settings = {
            "model": gt_llm,
            "coherence_model": self.config.options.get("coherence_model", gt_llm),
            "max_steps": self.config.options.get("max_steps", 100),
            "max_errors": self.config.options.get("max_errors", 10),
            "write_only": write_only,
        }

    def validate_task(self, task_dict, db_entities):
        hints = ActionHints(
            task_dict["reference_actions"], self.config.load_policy(), coverage_p=self.p, shuffle=self.shuffle
        ).system_prompt
        result = self.implementation.validate(
            copy.deepcopy(task_dict), copy.deepcopy(db_entities), hints=hints, settings=self.settings
        )
        if not isinstance(result, ValidationResult):
            raise InfrastructureError("EA task service returned an invalid validation result")
        if result.success and result.native_task is None:
            raise InfrastructureError("EA task service accepted a candidate without a native task")
        if result.candidate is not None:
            if result.candidate["reference_actions"] != task_dict["reference_actions"]:
                raise InfrastructureError("EA task service changed the reference actions used by the supplied hints")
        directory = self.config.options.get("evidence_dir", "artifacts/validation")
        if directory:
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            target = path / f"validation-{uuid.uuid4().hex}.json"
            simulation = result.gt_simulation
            if isinstance(simulation, dict):
                if simulation.get("artifact_path"):
                    if not Path(simulation["artifact_path"]).is_file():
                        raise InfrastructureError("EA task service returned a missing hinted trace")
                else:
                    trace_path = path / f"hinted-{uuid.uuid4().hex}.json"
                    simulation = {**simulation, "supplied_hints": hints, "artifact_path": str(trace_path.resolve())}
                    trace_path.write_text(json.dumps(simulation, indent=2) + "\n")
                    result.gt_simulation = simulation
            receipt = {
                "schema_version": 2,
                "prepared_inputs": self.config.to_spec(),
                "candidate": result.candidate if result.candidate is not None else task_dict,
                "fixture": result.fixture if result.fixture is not None else db_entities,
                "result": {
                    "success": result.success,
                    "failed_step": result.failed_step,
                    "error": result.error,
                    "unsupported": result.unsupported,
                    "repair_scope": result.repair_scope,
                },
                "hint_policy": {"p": self.p, "shuffle": self.shuffle},
                "hinted_trace": simulation.get("artifact_path") if isinstance(simulation, dict) else None,
            }
            target.write_text(json.dumps(receipt, indent=2) + "\n")
            result.evidence_path = str(target)
        return result
