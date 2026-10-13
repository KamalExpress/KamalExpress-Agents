"""
Alamia-Skills-System adapters package.
"""
from .base import BaseDetector
from .stack_detector import StackDetector
from .test_detector import TestDetector
from .memory_detector import MemoryDetector
from .agent_detector import AgentDetector

__all__ = [
    "BaseDetector",
    "StackDetector",
    "TestDetector",
    "MemoryDetector",
    "AgentDetector",
]
