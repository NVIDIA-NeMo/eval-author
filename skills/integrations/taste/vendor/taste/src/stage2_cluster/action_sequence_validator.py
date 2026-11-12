"""
Validates action sequences for viability using an LLM.

Checks that a sequence is realistic, pragmatic, policy-compliant,
environment-valid, and logically coherent.
"""

import json
from dataclasses import dataclass
from typing import List

from src.common.llm_response_parser import LLMResponseParser
from src.common.sequence_evidence import error_metadata, validation_payload
from src.common.tool_spec_retriever import ToolsSpecRetriever


@dataclass
class ValidationResult:
    """Result of action sequence validation."""

    valid: bool
    reason: str
    problematic_indices: List[int]
    evidence_id: int | None = None


class ActionSequenceValidator:
    """
    Validates whether an action sequence is viable for the benchmark.

    Uses an LLM to check that the sequence is:
    - Realistic (plausible interaction)
    - Pragmatic (correct ordering, prerequisites satisfied)
    - Policy-compliant (allowed under domain policy)
    - Environment-valid (valid tools, respects data model)
    - Logical (coherent cause-and-effect flow)
    """

    def __init__(
        self,
        domain: str | None = None,
        model_name: str = "vertex_ai/gemini-3-flash-preview",
        max_output_tokens: int = 16384,
        save_prompts: bool = False,
        *,
        domain_config,
        temperature: float = 0.0,
        evidence=None,
    ):
        if domain is not None and domain != domain_config.domain:
            raise ValueError("Domain does not match the prepared input configuration")
        self.domain = domain_config.domain
        self.evidence = evidence
        self.policy = domain_config.load_policy()
        self.tool_spec_json = ToolsSpecRetriever(path=domain_config.tool_spec_path).get_tool_spec_json()
        self.prompt_manager = domain_config.prompt_manager()
        # Note: ``custom_config`` below is honored only on the Vertex AI path of
        # ``LLMCaller``; on the litellm path the temperature comes from the
        # ``LLMCaller`` constructor. Pass it explicitly so validation is
        # deterministic regardless of which provider is selected.
        self._generation_config = None
        self.llm_caller = domain_config.make_llm(
            model_name=model_name,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            save_prompts=save_prompts,
            **({"evidence": evidence} if evidence is not None else {}),
        )

    def validate(self, action_sequence: List[str]) -> ValidationResult:
        """
        Validate an action sequence for viability.

        Args:
            action_sequence: List of action names in order

        Returns:
            ValidationResult with valid=True/False and reason string
        """
        validation_id = None
        if self.evidence is not None:
            validation_id = self.evidence.record("validation_started", sequence=action_sequence)
        if not action_sequence:
            result = ValidationResult(
                valid=True,
                reason="Empty sequence contains no tool actions to validate.",
                problematic_indices=[],
            )
            if self.evidence is not None:
                result.evidence_id = self.evidence.record(
                    "validation_decision",
                    validation_id=validation_id,
                    decision=validation_payload(result),
                    model_called=False,
                )
            return result

        prompt = self.prompt_manager.get_prompt("validate_action_sequence").format(
            action_sequence=json.dumps(action_sequence),
            policy=self.policy,
            tool_spec=self.tool_spec_json,
        )

        response = None
        try:
            response = self.llm_caller.call(
                prompt=prompt,
                stage_name="validate_action_sequence",
                custom_config=self._generation_config,
                **({"evidence_context": {"validation_id": validation_id}} if self.evidence is not None else {}),
            )
            parsed = LLMResponseParser.extract_json(response)
            valid = parsed.get("valid", False)
            reason = parsed.get("reason", "No reason provided.")
            raw_indices = parsed.get("problematic_indices", [])
            # Sanitise: keep only valid integer indices within range
            seq_len = len(action_sequence)
            problematic_indices = [int(i) for i in raw_indices if isinstance(i, (int, float)) and 0 <= int(i) < seq_len]
        except Exception as error:
            if self.evidence is not None:
                self.evidence.record(
                    "validation_failed",
                    validation_id=validation_id,
                    response_text=response,
                    error=error_metadata(error),
                )
            raise
        result = ValidationResult(valid=valid, reason=reason, problematic_indices=problematic_indices)
        if self.evidence is not None:
            result.evidence_id = self.evidence.record(
                "validation_decision",
                validation_id=validation_id,
                response_text=response,
                parsed=parsed,
                decision=validation_payload(result),
                model_called=True,
            )
        return result
