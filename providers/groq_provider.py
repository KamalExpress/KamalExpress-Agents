"""
providers/groq_provider.py
──────────────────────────
Groq Cloud LPU provider — Ultra-fast sub-second LLM inference with native tool calling.

Env vars:
    GROQ_API_KEY=gsk_...
    GROQ_DEFAULT_MODEL=qwen/qwen3.8-27b
"""
from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI

from .base import AgentRole, AIServiceProvider
from config.settings import get_settings

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class GroqProvider(AIServiceProvider):
    """
    Groq LPU ultra-fast provider.
    """

    @property
    def name(self) -> str:
        return "groq"

    def _model_for_role(self, role: AgentRole) -> str:
        cfg = get_settings().groq
        role_map: dict[str, str] = {
            "orchestrator": cfg.orchestrator_model,
            "visa": cfg.visa_model,
            "appointments": cfg.appointments_model,
            "default": cfg.default_model,
        }
        return role_map.get(role, cfg.default_model)

    def get_llm(
        self,
        role: AgentRole = "default",
        *,
        temperature: float = 0.2,
        streaming: bool = True,
        max_tokens: int = 500,
        **kwargs: Any,
    ) -> ChatOpenAI:
        cfg = get_settings().groq
        model = self._model_for_role(role)
        return ChatOpenAI(
            model=model,
            api_key=cfg.api_key,
            base_url=cfg.base_url or GROQ_BASE_URL,
            temperature=temperature,
            streaming=streaming,
            max_tokens=max_tokens,
            **kwargs,
        )

    def health_check(self) -> bool:
        """Verify Groq API connectivity."""
        try:
            llm = self.get_llm("default", max_tokens=10)
            llm.invoke("ping")
            return True
        except Exception:
            return False
