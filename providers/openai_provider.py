"""
providers/openai_provider.py
────────────────────────────
OpenAI cloud provider — reads keys and model names from env vars.
Also usable for any OpenAI-compatible gateway (Azure, Together, etc.)
by pointing OPENAI_BASE_URL at a different endpoint.
"""
from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from .base import AgentRole, AIServiceProvider
from config.settings import get_settings


class OpenAIProvider(AIServiceProvider):
    """
    OpenAI cloud provider.

    Env vars:
        OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_*_MODEL
    """

    @property
    def name(self) -> str:
        return "openai"

    def _model_for_role(self, role: AgentRole) -> str:
        cfg = get_settings().openai
        role_map: dict[str, str] = {
            "orchestrator": cfg.orchestrator_model,
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
    ) -> ChatOpenAI:
        cfg = get_settings().openai
        model = self._model_for_role(role)
        return ChatOpenAI(
            model=model,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            temperature=temperature,
            streaming=streaming,
            **kwargs,
        )

    def get_embed_model(self) -> OpenAIEmbeddings:
        cfg = get_settings().openai
        return OpenAIEmbeddings(
            model="text-embedding-3-small",
            api_key=cfg.api_key,
        )
