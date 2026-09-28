"""
providers/ollama_provider.py
────────────────────────────
Local Ollama provider — uses the OpenAI-compatible /v1 endpoint.
No API key required; set api_key="ollama" as a dummy.
"""
from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings

from .base import AgentRole, AIServiceProvider
from config.settings import get_settings


class OllamaProvider(AIServiceProvider):
    """
    Serves local Ollama models via the OpenAI-compatible endpoint.

    Model selection per role comes from env vars:
        OLLAMA_ORCHESTRATOR_MODEL, OLLAMA_VISA_MODEL,
        OLLAMA_APPOINTMENTS_MODEL, OLLAMA_DEFAULT_MODEL
    """

    @property
    def name(self) -> str:
        return "ollama"

    def _model_for_role(self, role: AgentRole) -> str:
        cfg = get_settings().ollama
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
        cfg = get_settings().ollama
        model = self._model_for_role(role)
        return ChatOpenAI(
            model=model,
            base_url=cfg.base_url,
            api_key=cfg.api_key,          # dummy; Ollama ignores it
            temperature=temperature,
            streaming=streaming,
            **kwargs,
        )

    def get_embed_model(self) -> OpenAIEmbeddings:
        """Return an OpenAI-compatible embedding client pointed at Ollama/bge-m3."""
        cfg_main = get_settings()
        cfg_ollama = cfg_main.ollama
        cfg_rag = cfg_main.rag
        return OpenAIEmbeddings(
            model=cfg_rag.embed_model,
            base_url=cfg_rag.embed_base_url,
            api_key=cfg_ollama.api_key,
        )

    def health_check(self) -> bool:
        """Check Ollama is reachable without burning tokens."""
        import urllib.request
        try:
            url = get_settings().ollama.base_url.replace("/v1", "/api/tags")
            urllib.request.urlopen(url, timeout=3)
            return True
        except Exception:
            return False
