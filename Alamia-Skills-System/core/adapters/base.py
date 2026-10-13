"""
Base interface for Alamia-Skills-System adapters and detectors.
All adapters must be completely project-agnostic.
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict


class BaseDetector(ABC):
    """Abstract base class for all discovery detectors."""

    @abstractmethod
    def detect(self, project_root: Path) -> Dict[str, Any]:
        """Inspect the given project root and return detected metadata."""
        pass
