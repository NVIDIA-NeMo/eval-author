# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Synthetic ledger task used to exercise NeMo Gym proposal authoring and execution."""

import importlib
import json
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

# This fixture runs in the separately installed NeMo Gym environment.
resources = importlib.import_module("nemo_gym.base_resources_server")
VerifierFixture = importlib.import_module("nemo_gym.verifier_fixture").VerifierFixture


class LedgerConfig(resources.BaseResourcesServerConfig):
    pass


class TotalRequest(BaseModel):
    amounts: list[int] = Field(min_length=1)


class TotalResponse(BaseModel):
    total: int


class LedgerVerifyRequest(resources.BaseVerifyRequest):
    model_config = ConfigDict(extra="allow")
    amounts: list[int]


class LedgerVerifier:
    async def verify(self, body: LedgerVerifyRequest):
        calls = [item for item in body.response.output if item.type == "function_call"]
        correct_call = False
        for call in calls:
            try:
                correct_call |= call.name == "ledger_total" and json.loads(call.arguments) == {"amounts": body.amounts}
            except (TypeError, ValueError):
                continue
        correct_answer = body.response.output_text.strip() == str(sum(body.amounts))
        reward = float(correct_call and correct_answer)
        return resources.BaseVerifyResponse(**body.model_dump(), reward=reward)


class LedgerResourcesServer(LedgerVerifier, resources.SimpleResourcesServer):
    config: LedgerConfig

    def setup_webserver(self) -> FastAPI:
        app = super().setup_webserver()
        app.post("/ledger_total")(self.ledger_total)
        return app

    async def ledger_total(self, body: TotalRequest) -> TotalResponse:
        return TotalResponse(total=sum(body.amounts))


VERIFIER_FIXTURE = VerifierFixture(
    server_factory=LedgerVerifier,
    request_model=LedgerVerifyRequest,
    cases_path=Path(__file__).parent / "tests" / "verifier_cases.jsonl",
)


if __name__ == "__main__":
    LedgerResourcesServer.run_webserver()
