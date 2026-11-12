"""
Abstract base class for models that support checkpointing (save/load).
"""

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, TypeVar

T = TypeVar("T", bound="CheckpointableModel")


class CheckpointableModel(ABC):
    """
    Abstract base for models that can save and load state to/from JSON files.

    Subclasses must implement:
      - _serialize_state(): return a JSON-serializable dict of full state
      - _restore_from_state(state): restore instance attributes from that dict
      - checkpoint_filename(): return a descriptive filename for checkpoints
    """

    def save_state(self, path: str) -> None:
        """Save full state to a JSON file."""
        state = self._serialize_state()
        with open(path, "w") as f:
            json.dump(state, f, indent=2)

    @classmethod
    def load_state(cls: type[T], path: str) -> T:
        """Load state from a JSON file and return a reconstructed instance."""
        with open(path, "r") as f:
            state = json.load(f)
        instance = cls.__new__(cls)
        cls._restore_from_state(instance, state)
        return instance

    @abstractmethod
    def _serialize_state(self) -> Dict[str, Any]:
        """Return full state as a JSON-serializable dict."""
        ...

    @classmethod
    @abstractmethod
    def _restore_from_state(cls, instance: "CheckpointableModel", state: Dict[str, Any]) -> None:
        """Restore instance attributes from a loaded state dict."""
        ...

    @abstractmethod
    def checkpoint_filename(self) -> str:
        """Generate a descriptive checkpoint filename."""
        ...
