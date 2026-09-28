"""
config/settings.py
──────────────────
Central configuration loaded from environment variables / .env file.
All runtime config lives here — never import raw os.environ elsewhere.

Supported AI_PROVIDER values:
  groq        — Groq Cloud LPU (ultra-fast sub-second responses, recommended)
  sambanova   — SambaNova Cloud (SN40L high-speed inference)
  bitnet      — Alamia Connect Cloud (no local GPU/Ollama required)
  ollama      — local Ollama (local GPU/CPU)
  openrouter  — openrouter.ai (cheap, multi-model, pay-per-token)
  openai      — OpenAI directly
  anthropic   — Anthropic directly
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ─────────────────────────────────────────────────────────────
# Sub-settings groups
# ─────────────────────────────────────────────────────────────

class GroqSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GROQ_", env_file=".env", extra="ignore")

    api_key: str = ""
    base_url: str = "https://api.groq.com/openai/v1"
    default_model: str = "qwen/qwen3.8-27b"
    orchestrator_model: str = "qwen/qwen3.8-27b"
    visa_model: str = "qwen/qwen3.8-27b"
    appointments_model: str = "qwen/qwen3.8-27b"


class SambaNovaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SAMBANOVA_", env_file=".env", extra="ignore")

    api_key: str = ""
    base_url: str = "https://api.sambanova.ai/v1"
    default_model: str = "Meta-Llama-3.3-70B-Instruct"
    orchestrator_model: str = "Meta-Llama-3.3-70B-Instruct"
    visa_model: str = "Meta-Llama-3.3-70B-Instruct"
    appointments_model: str = "Meta-Llama-3.3-70B-Instruct"


class BitNetSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BITNET_", env_file=".env", extra="ignore")

    server_url: str = "https://ai.alamiaconnect.com/v1"
    api_key: str = "51129693340"
    default_task: str = "dialogue"
    orchestrator_task: str = "dialogue"
    visa_task: str = "extraction"
    appointments_task: str = "dialogue"
    timeout_seconds: int = 60


class OllamaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OLLAMA_", env_file=".env", extra="ignore")

    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    default_model: str = "qwen2.5:7b"
    orchestrator_model: str = "gemma3:1b-it-qat"
    visa_model: str = "qwen2.5:7b"
    appointments_model: str = "qwen3.5:4b"


class OpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENAI_", env_file=".env", extra="ignore")

    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    default_model: str = "gpt-4o-mini"
    orchestrator_model: str = "gpt-4o-mini"
    visa_model: str = "gpt-4o"
    appointments_model: str = "gpt-4o-mini"


class AnthropicSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ANTHROPIC_", env_file=".env", extra="ignore")

    api_key: str = ""
    default_model: str = "claude-3-5-haiku-20241022"
    visa_model: str = "claude-3-5-sonnet-20241022"
    appointments_model: str = "claude-3-5-haiku-20241022"


class OpenRouterSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENROUTER_", env_file=".env", extra="ignore")

    api_key: str = ""
    site_url: str = "https://kamalexpress.com"
    site_name: str = "Kamal Express"

    # Cheap-but-capable model defaults.
    # Costs are approximate per 1M tokens (input+output blended) at time of writing.
    #   llama-3.1-8b-instruct  ≈ $0.06   — great for fast triage
    #   gpt-4o-mini            ≈ $0.15   — best quality/cost for structured tasks
    #   mistral-7b-instruct    ≈ $0.06   — solid general fallback
    #   claude-3-haiku         ≈ $0.25   — best for nuanced conversations
    default_model: str = "openai/gpt-4o-mini"
    orchestrator_model: str = "meta-llama/llama-3.1-8b-instruct"   # fastest & cheapest for routing
    visa_model: str = "openai/gpt-4o-mini"                         # structured doc checklist
    appointments_model: str = "openai/gpt-4o-mini"                 # form-filling & scheduling


class CaptchaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAPTCHA_", env_file=".env", extra="ignore")

    provider: Literal["2captcha", "capsolver", "anticaptcha", "none"] = "none"
    api_key: str = ""
    timeout: int = 120
    retry_attempts: int = 3


class ProxySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROXY_", env_file=".env", extra="ignore")

    provider: Literal["none", "static", "rotating", "brightdata", "oxylabs", "smartproxy"] = "none"
    static_url: str = ""
    rotate_endpoint: str = ""
    username: str = ""
    password: str = ""
    country: str = "PK"

    # BrightData
    brightdata_zone: str = ""
    brightdata_host: str = "brd.superproxy.io"
    brightdata_port: int = 22225


class BrowserSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BROWSER_", env_file=".env", extra="ignore")

    # "cdp" = attach to system Chrome (preferred)
    # "local" = launch headless Chromium (fallback)
    mode: str = "cdp"
    cdp_url: str = "ws://localhost:9222"
    chrome_profile: str = "Default"

    # Local mode only
    headless: bool = True
    timeout_ms: int = 30_000
    slow_mo_ms: int = 0
    user_data_dir: str = "./browser_profiles"


class RagSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    chroma_persist_dir: str = "./rag/chroma_db"
    embed_model: str = "bge-m3:latest"
    embed_base_url: str = "http://localhost:11434/v1"


# ─────────────────────────────────────────────────────────────
# Root settings
# ─────────────────────────────────────────────────────────────

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Provider selector
    ai_provider: Literal["groq", "sambanova", "bitnet", "ollama", "openai", "anthropic", "openrouter"] = "groq"

    # API server
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    api_reload: bool = True

    # Logging
    log_level: str = "INFO"
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "kamal-express"

    # Nested settings (instantiated on first access)
    @property
    def groq(self) -> GroqSettings:
        return GroqSettings()

    @property
    def sambanova(self) -> SambaNovaSettings:
        return SambaNovaSettings()

    @property
    def bitnet(self) -> BitNetSettings:
        return BitNetSettings()

    @property
    def ollama(self) -> OllamaSettings:
        return OllamaSettings()

    @property
    def openai(self) -> OpenAISettings:
        return OpenAISettings()

    @property
    def anthropic(self) -> AnthropicSettings:
        return AnthropicSettings()

    @property
    def openrouter(self) -> OpenRouterSettings:
        return OpenRouterSettings()

    @property
    def captcha(self) -> CaptchaSettings:
        return CaptchaSettings()

    @property
    def proxy(self) -> ProxySettings:
        return ProxySettings()

    @property
    def browser(self) -> BrowserSettings:
        return BrowserSettings()

    @property
    def rag(self) -> RagSettings:
        return RagSettings()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
