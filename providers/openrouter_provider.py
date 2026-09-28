"""
providers/openrouter_provider.py
──────────────────────────────────
OpenRouter provider — routes through https://openrouter.ai/api/v1
which is fully OpenAI-API-compatible, so we reuse ChatOpenAI.

Why OpenRouter for Kamal Express?
  - Single API key across 200+ models
  - Pay-per-token; no subscription needed
  - Cheap models (Llama, Mistral, Gemma, GPT-4o-mini, Claude Haiku)
    are perfectly adequate for travel Q&A, form filling, and routing
  - Automatic fallback to next cheapest model if one is rate-limited

Recommended models for this use-case (cheapest → best quality):
  Triage / orchestrator:  meta-llama/llama-3.1-8b-instruct   ~$0.06 / 1M tok
  Visa / appointments:    openai/gpt-4o-mini                  ~$0.15 / 1M tok
  Fallback / structured:  mistralai/mistral-7b-instruct       ~$0.06 / 1M tok
  If more reasoning needed: anthropic/claude-3-haiku-20240307 ~$0.25 / 1M tok
"""
from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI

from .base import AgentRole, AIServiceProvider
from config.settings import get_settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(AIServiceProvider):
    """
    OpenRouter provider — cheap, multi-model, OpenAI-compatible.

    Env vars:
        OPENROUTER_API_KEY
        OPENROUTER_*_MODEL
        OPENROUTER_SITE_URL   (optional, shown in OR dashboard)
        OPENROUTER_SITE_NAME  (optional, shown in OR dashboard)
    """

    @property
    def name(self) -> str:
        return "openrouter"

    def _model_for_role(self, role: AgentRole) -> str:
        cfg = get_settings().openrouter
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
        cfg = get_settings().openrouter
        model = self._model_for_role(role)

        # OpenRouter requires these headers for usage tracking
        default_headers = {
            "HTTP-Referer": cfg.site_url or "https://kamalexpress.com",
            "X-Title":      cfg.site_name or "Kamal Express",
        }

        return ChatOpenAI(
            model=model,
            api_key=cfg.api_key,
            base_url=OPENROUTER_BASE_URL,
            temperature=temperature,
            streaming=streaming,
            default_headers=default_headers,
            **kwargs,
        )

    def health_check(self) -> bool:
        """Ping OpenRouter models endpoint to verify key is valid."""
        import urllib.request
        try:
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {get_settings().openrouter.api_key}"},
            )
            urllib.request.urlopen(req, timeout=5)
            return True
        except Exception:
            return False
