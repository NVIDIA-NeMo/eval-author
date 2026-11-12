"""
Load tool spec from JSON and provide access to action names and full spec.
Assumes all tool specs share the same structure (tool name -> parameters).
"""

import json
from typing import Any, Dict, List, Optional


class ToolsSpecRetriever:
    """Loads tool spec from JSON file."""

    def __init__(self, path: str):
        self._path = path
        self._spec: Optional[Dict[str, Any]] = None

    def _load(self) -> Dict[str, Any]:
        if self._spec is not None:
            return self._spec
        with open(self._path, "r") as f:
            spec = json.load(f)
        self._spec = spec
        return spec

    def get_tool_spec(self) -> List[List[str]]:
        """Returns list of actions as [[action_name], ...]."""
        return [[name] for name in self._load().keys()]

    def get_tool_spec_json(self) -> str:
        """Returns full tool spec as JSON string."""
        return json.dumps(self._load(), indent=2)

    def get_action_types(self) -> Dict[str, str]:
        """Returns mapping of action_name -> type ('READ', 'WRITE', 'GENERIC')."""
        return {name: spec.get("type", "GENERIC") for name, spec in self._load().items()}

    def get_action_params(self) -> Dict[str, Dict[str, Any]]:
        """Returns mapping of action_name -> {param_name: param_meta}.

        Used by deterministic arg-shape validation: any action argument whose
        name is not in this mapping is a hallucinated arg.
        """
        return {name: (spec.get("parameters") or {}) for name, spec in self._load().items()}
