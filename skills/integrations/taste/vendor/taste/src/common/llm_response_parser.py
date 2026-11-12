"""
LLM Response Parser

Utilities for parsing and extracting JSON from LLM responses.
Handles common issues like markdown code blocks and trailing commas.
"""

import json
import re
from typing import Any, Dict


class LLMResponseParser:
    """
    Utility class for parsing and extracting JSON from LLM responses.
    """

    @staticmethod
    def _remove_trailing_commas(json_str: str) -> str:
        """
        Remove trailing commas from JSON string.
        LLMs often produce JSON with trailing commas which is invalid.

        Args:
            json_str: JSON string potentially with trailing commas

        Returns:
            JSON string with trailing commas removed
        """
        # Remove trailing commas before closing brackets/braces
        # Handles: [1, 2, 3,] -> [1, 2, 3] and {"a": 1,} -> {"a": 1}
        json_str = re.sub(r",(\s*[\]\}])", r"\1", json_str)
        return json_str

    @staticmethod
    def extract_json(response: str) -> Dict[str, Any]:
        """
        Extract JSON from LLM response, handling markdown code blocks.

        Args:
            response: Raw LLM response string

        Returns:
            Parsed JSON as dictionary

        Raises:
            ValueError: If JSON parsing fails
        """
        # Try to extract JSON from response (might have markdown code blocks)
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        response = response.strip()

        # Try direct parse
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Try removing trailing commas
        cleaned = LLMResponseParser._remove_trailing_commas(response)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON object embedded in narrative text
        # Find the first { and last } to extract the JSON block
        first_brace = response.find("{")
        last_brace = response.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            candidate = response[first_brace : last_brace + 1]
            candidate = LLMResponseParser._remove_trailing_commas(candidate)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Failed to parse LLM response as JSON.\nResponse: {response}")
