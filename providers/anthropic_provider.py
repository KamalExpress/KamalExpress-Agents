"""
providers/anthropic_provider.py
────────────────────────────────
Anthropic Claude provider.
"""
from __future__ import annotations

from typing import Any

from langchain_anthropic import ChatAnthropic

from .base import AgentRole, AIServiceProvider
from config.settings import get_settings


class AnthropicProvider(AIServiceProvider):
    """
    Anthropic Claude provider.

    Env vars:
        ANTHROPIC_API_KEY, ANTHROPIC_*_MODEL
    """

    @property
    def name(self) -> str:
        return "anthropic"

    def _model_for_role(self, role: AgentRole) -> str:
        cfg = get_settings().anthropic
        role_map: dict[str, str] = {
            "orchestrator": cfg.default_model,
            "visa":         cfg.visa_model,
            "appointments": cfg.appointments_model,
            "default":      cfg.default_model,
        }
        return role_map.get(role, cfg.default_model)

    def get_llm(
        self,
        role: AgentRole = "default",
        *,
        temperature: float = 0.0,
        streaming: bool = True,
        **kwargs: Any,
    ) -> ChatAnthropic:
        cfg = get_settings().anthropic
        model = self._model_for_role(role)
        return ChatAnthropic(
            model=model,
            api_key=cfg.api_key,
            temperature=temperature,
            streaming=streaming,
            **kwargs,
        )

    # Anthropic does not offer embedding models; delegate to Ollama
    def get_embed_model(self):
        raise NotImplementedError(
            "Anthropic has no embedding API. "
            "Use OllamaProvider().get_embed_model() for local embeddings."
        )
