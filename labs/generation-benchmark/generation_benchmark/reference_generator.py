# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed `generation.settings`, operator answers, prompt fields, and G image checks for the reference generator."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal, Self

from metric_benchmark.models import Digest, Model, Text
from pydantic import Field, StringConstraints, field_validator, model_validator

from generation_benchmark.models import InputRef, SchemaModel, read_yaml

SOURCE_LABEL = "org.nvidia.eval-author.generator-source-sha256"
OPERATOR_ANSWERS_SCHEMA = "nemo.eval_author.generation_operator_answers.v1"
DEFAULT_ENTRYPOINT = ("python3", "/opt/generation/entrypoint.py")
INTERNAL_NETWORK_ERROR = "internal network requires the relay (Q1)"
RESERVED_ENV = frozenset({"HARBOR_SIDECAR_URL", "HARBOR_SIDECAR_TOKEN", "CODEX_HOME"})
PROMPT_FIELDS = frozenset(
    {
        "tool_name",
        "task_slug",
        "proposal_path",
        "draft_path",
        "measurements_path",
        "report_path",
        "harbor_python",
        "agent_id",
        "wrong_answer_agent",
        "wrong_answer_script",
        "authorizations",
    }
)
_PROMPT_FIELD_RE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")

EnvName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]


class ReferenceSettings(Model):
    """`generation.settings` of the reference generator; unknown fields are rejected."""

    image_id: Digest
    prompt: InputRef
    operator_answers: InputRef
    entrypoint: list[Text] = Field(default_factory=lambda: list(DEFAULT_ENTRYPOINT), min_length=1)
    agent_argv: list[Text] = Field(min_length=1)
    provider_env: list[EnvName] = Field(default_factory=list)
    network: Literal["default", "internal"] = "default"

    @field_validator("provider_env")
    @classmethod
    def no_reserved_env(cls, value: list[str]) -> list[str]:
        reserved = sorted(set(value) & RESERVED_ENV)
        if reserved:
            raise ValueError(f"provider_env must not name launcher variables: {', '.join(reserved)}")
        if len(set(value)) != len(value):
            raise ValueError("provider_env names must be unique")
        return value


class Answer(Model):
    status: Literal["confirmed", "verified"]
    source: Text


class OperatorPrerequisites(Model):
    """Prerequisites the operator answers; the entrypoint adds `python` and `runtime` from `locate_runtime.py`."""

    code_access: Answer
    file_writes: Answer
    command_execution: Answer
    skills: Answer
    ethos_skills: Answer
    environment: Answer
    services: Answer
    data: Answer
    agent_connection: Answer
    model_credentials: Answer


class Authorizations(Model):
    docker: bool
    harbor: bool
    real_agent_runs: bool
    model_calls: bool


class CorpusRef(Model):
    source: Text


class Scenarios(Model):
    positive: Text
    negative: Text


class ExpandScope(Model):
    request: Text
    corpus: CorpusRef
    priorities: Text
    cost_constraints: Text
    examples_per_behavior: int = Field(ge=1)
    variations_and_difficulty: Text
    scenarios: Scenarios
    expected_outcomes: Text
    attempts_per_task: int = Field(ge=1)
    pilot_or_expansion: Literal["pilot", "expansion"]
    authorizations: Authorizations
    decision_source: Text
    confirmed: bool
    needs_review: bool


class Scopes(Model):
    expand: ExpandScope


class JourneyStatus(Model):
    expand: Literal["pending", "in_progress"]


class Blocker(Model):
    id: Text
    phase: Text
    message: Text


class JourneyUpdate(Model):
    prerequisites: OperatorPrerequisites
    scopes: Scopes
    journey_status: JourneyStatus
    blockers: list[Blocker] = Field(default_factory=list)
    pending_question: Text | None = None
    next_handoff: Text | None = None


class EthosConfirmation(Model):
    source: Text
    validation: Text


class OperatorAnswers(SchemaModel):
    """`operator-answers.yaml`: the ETHOS confirmation and the `journey_update` the entrypoint applies."""

    schema_id: Literal["nemo.eval_author.generation_operator_answers.v1"] = Field(
        default=OPERATOR_ANSWERS_SCHEMA,
        alias="schema",
    )
    ethos_confirmation: EthosConfirmation
    journey_update: JourneyUpdate
    authorizations: Authorizations

    @model_validator(mode="after")
    def authorizations_match_scope(self) -> Self:
        if self.authorizations != self.journey_update.scopes.expand.authorizations:
            raise ValueError("authorizations differs from journey_update.scopes.expand.authorizations")
        return self


def load_operator_answers(path: Path) -> OperatorAnswers:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"operator answers must be a regular file: {path}")
    return OperatorAnswers.model_validate(read_yaml(path))


def prompt_fields(template: str) -> set[str]:
    return set(_PROMPT_FIELD_RE.findall(template))


def check_prompt_template(template: str) -> None:
    unknown = sorted(prompt_fields(template) - PROMPT_FIELDS)
    if unknown:
        raise ValueError(
            f"prompt names unknown field(s): {', '.join(unknown)}; known: {', '.join(sorted(PROMPT_FIELDS))}"
        )


def fill_prompt(template: str, values: dict[str, str]) -> str:
    check_prompt_template(template)
    missing = sorted(prompt_fields(template) - set(values))
    if missing:
        raise ValueError(f"no value for prompt field(s): {', '.join(missing)}")
    return _PROMPT_FIELD_RE.sub(lambda match: values[match.group(1)], template)


@dataclass(frozen=True)
class ImageInfo:
    id: str
    labels: dict[str, str] = field(default_factory=dict)


ImageInspector = Callable[[str], ImageInfo | None]


def docker_image_inspect(image_id: str, *, docker: str = "docker") -> ImageInfo | None:
    """Return the image's id and labels, or None when Docker cannot find it."""
    try:
        result = subprocess.run(
            [docker, "image", "inspect", "--format", "{{json .}}", image_id],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("Id"), str):
        return None
    labels = (payload.get("Config") or {}).get("Labels") or {}
    return ImageInfo(id=payload["Id"], labels={str(k): str(v) for k, v in labels.items()})


def image_matches(image_id: str, source_digest: str | None, inspect: ImageInspector = docker_image_inspect) -> bool:
    """True when Docker has *image_id* and its source label equals *source_digest*."""
    if source_digest is None:
        return False
    info = inspect(image_id)
    return info is not None and info.id == image_id and info.labels.get(SOURCE_LABEL) == source_digest


def image_mismatch_message(image_id: object) -> str:
    return f"G image {image_id} missing or built from different skills; rebuild and re-sign"
