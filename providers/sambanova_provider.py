"""
providers/sambanova_provider.py
───────────────────────────────
SambaNova Cloud provider — high-speed inference on custom SN40L chips.

Env vars:
    SAMBANOVA_API_KEY=33e...
    SAMBANOVA_DEFAULT_MODEL=Meta-Llama-3.3-70B-Instruct
"""
from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI

from .base import AgentRole, AIServiceProvider
from config.settings import get_settings

SAMBANOVA_BASE_URL = "https://api.sambanova.ai/v1"


class SambaNovaProvider(AIServiceProvider):
    """
    SambaNova cloud AI provider.
    """

    @property
    def name(self) -> str:
        return "sambanova"

    def _model_for_role(self, role: AgentRole) -> str:
        cfg = get_settings().sambanova
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
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> ChatOpenAI:
        cfg = get_settings().sambanova
        model = self._model_for_role(role)
        return ChatOpenAI(
            model=model,
            api_key=cfg.api_key,
            base_url=cfg.base_url or SAMBANOVA_BASE_URL,
            temperature=temperature,
            streaming=streaming,
            max_tokens=max_tokens,
            **kwargs,
        )

    def health_check(self) -> bool:
        """Verify SambaNova API connectivity."""
        try:
            llm = self.get_llm("default", max_tokens=10)
            llm.invoke("ping")
            return True
        except Exception:
            return False
