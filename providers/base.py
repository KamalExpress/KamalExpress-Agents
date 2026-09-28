"""
providers/base.py
─────────────────
Abstract AIServiceProvider — extend this to create any LLM backend.

Pattern:
    provider = OllamaProvider()          # or OpenAIProvider(), AnthropicProvider()
    llm      = provider.get_llm("visa")  # returns a LangChain BaseChatModel
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel

AgentRole = Literal["orchestrator", "visa", "appointments", "default"]


class AIServiceProvider(ABC):
    """
    Base class for all AI service providers.

    Subclasses must implement:
        - get_llm(role)  → BaseChatModel
        - get_embed_model() → Any  (optional override)
        - name           → str property
    """

    # ── Public API ────────────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. 'ollama', 'openai')."""

    @abstractmethod
    def get_llm(
        self,
        role: AgentRole = "default",
        *,
        temperature: float = 0.0,
        streaming: bool = True,
        **kwargs: Any,
    ) -> BaseChatModel:
        """
        Return a configured LangChain chat model for the given agent role.

        Args:
            role:        Which agent is requesting the model.
                         Providers use this to look up role-specific model names.
            temperature: Sampling temperature (0 = deterministic).
            streaming:   Enable streaming callbacks.
            **kwargs:    Additional model-specific parameters.
        """

    def get_embed_model(self) -> Any:
        """
        Return an embedding model instance.
        Default raises NotImplementedError — override when supported.
        """
        raise NotImplementedError(
            f"Provider '{self.name}' does not support embeddings. "
            "Use OllamaProvider for local embeddings via bge-m3."
        )

    # ── Convenience helpers ───────────────────────────────────

    def __repr__(self) -> str:
        return f"<AIServiceProvider: {self.name}>"

    def health_check(self) -> bool:
        """
        Quick connectivity check — tries to instantiate an LLM and calls it.
        Returns True if healthy, False otherwise.
        Override in subclasses for a faster/cheaper check.
        """
        try:
            llm = self.get_llm("default")
            llm.invoke("ping")  # minimal call
            return True
        except Exception:
            return False
