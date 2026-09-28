"""
providers/__init__.py
─────────────────────
Factory: get_provider() reads AI_PROVIDER env var and returns the right instance.
"""
from __future__ import annotations

from functools import lru_cache

from .base import AIServiceProvider, AgentRole
from config.settings import get_settings


@lru_cache(maxsize=1)
def get_provider() -> AIServiceProvider:
    """
    Return a singleton provider based on the AI_PROVIDER env var.

    AI_PROVIDER=ollama      → OllamaProvider     (local, free)
    AI_PROVIDER=openrouter  → OpenRouterProvider (cheap cloud, recommended)
    AI_PROVIDER=openai      → OpenAIProvider
    AI_PROVIDER=anthropic   → AnthropicProvider
    """
    provider_name = get_settings().ai_provider

    if provider_name == "groq":
        from .groq_provider import GroqProvider
        return GroqProvider()
    elif provider_name == "sambanova":
        from .sambanova_provider import SambaNovaProvider
        return SambaNovaProvider()
    elif provider_name == "bitnet":
        from .bitnet_provider import BitNetProvider
        return BitNetProvider()
    elif provider_name == "ollama":
        from .ollama_provider import OllamaProvider
        return OllamaProvider()
    elif provider_name == "openrouter":
        from .openrouter_provider import OpenRouterProvider
        return OpenRouterProvider()
    elif provider_name == "openai":
        from .openai_provider import OpenAIProvider
        return OpenAIProvider()
    elif provider_name == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider()
    else:
        raise ValueError(
            f"Unknown AI_PROVIDER='{provider_name}'. "
            "Valid options: groq | sambanova | bitnet | ollama | openrouter | openai | anthropic"
        )


__all__ = ["AIServiceProvider", "AgentRole", "get_provider"]
