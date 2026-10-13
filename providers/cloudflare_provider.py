"""
providers/cloudflare_provider.py
──────────────────────────────────
Cloudflare Workers AI & AI Gateway provider.
Native OpenAI-compatible endpoint with automatic multi-provider fallback.

Env vars:
    CF_ACCOUNT_ID=...
    CF_WORKER_AI_TOKEN=...
    CF_DEFAULT_MODEL=@cf/meta/llama-3.1-8b-instruct-fast
    CF_USE_GATEWAY=false  (set true when AI Gateway permissions are enabled)
    CF_GATEWAY_ID=default
"""
from __future__ import annotations

import logging
from typing import Any, List

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from .base import AgentRole, AIServiceProvider
from config.settings import get_settings

logger = logging.getLogger(__name__)


class CloudflareProvider(AIServiceProvider):
    """
    Cloudflare Workers AI & AI Gateway provider.
    Runs fast multilingual inference via Cloudflare global edge network.
    """

    @property
    def name(self) -> str:
        return "cloudflare"

    def _model_for_role(self, role: AgentRole) -> str:
        cfg = get_settings().cloudflare
        role_map: dict[str, str] = {
            "orchestrator": cfg.orchestrator_model,
            "visa": cfg.visa_model,
            "appointments": cfg.appointments_model,
            "default": cfg.default_model,
        }
        return role_map.get(role, cfg.default_model)

    def _get_base_url(self) -> str:
        cfg = get_settings().cloudflare
        if cfg.use_gateway:
            return f"https://gateway.ai.cloudflare.com/v1/{cfg.account_id}/{cfg.gateway_id}/compat"
        return f"https://api.cloudflare.com/client/v4/accounts/{cfg.account_id}/ai/v1"

    def get_llm(
        self,
        role: AgentRole = "default",
        *,
        temperature: float = 0.2,
        streaming: bool = True,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> BaseChatModel:
        cfg = get_settings().cloudflare
        model = self._model_for_role(role)
        base_url = self._get_base_url()

        primary_llm = ChatOpenAI(
            model=model,
            api_key=cfg.worker_ai_token,
            base_url=base_url,
            temperature=temperature,
            streaming=streaming,
            max_tokens=max_tokens,
            **kwargs,
        )

        fallbacks: List[BaseChatModel] = []

        # 1. Fallback: OpenRouter if key is configured
        openrouter_cfg = get_settings().openrouter
        if openrouter_cfg.api_key and not openrouter_cfg.api_key.startswith("sk-or-v1-placeholder"):
            try:
                from .openrouter_provider import OpenRouterProvider
                or_provider = OpenRouterProvider()
                fallback_or = or_provider.get_llm(
                    role,
                    temperature=temperature,
                    streaming=streaming,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                fallbacks.append(fallback_or)
            except Exception as e:
                logger.warning(f"[cloudflare_provider] Could not configure OpenRouter fallback: {e}")

        # 2. Fallback: Groq alternative model (openai/gpt-oss-20b) if key is configured
        groq_cfg = get_settings().groq
        if groq_cfg.api_key:
            try:
                fallback_groq = ChatOpenAI(
                    model="openai/gpt-oss-20b",
                    api_key=groq_cfg.api_key,
                    base_url=groq_cfg.base_url or "https://api.groq.com/openai/v1",
                    temperature=temperature,
                    streaming=streaming,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                fallbacks.append(fallback_groq)
            except Exception as e:
                logger.warning(f"[cloudflare_provider] Could not configure Groq fallback: {e}")

        if fallbacks:
            return primary_llm.with_fallbacks(fallbacks)
        return primary_llm
