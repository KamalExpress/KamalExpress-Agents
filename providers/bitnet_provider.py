"""
providers/bitnet_provider.py
────────────────────────────
BitNet cloud AI provider connecting to Alamia Connect AI infrastructure.
Zero local GPU / Ollama required.

Env vars:
    BITNET_SERVER_URL=https://ai.alamiaconnect.com/v1
    BITNET_API_KEY=51129693340
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

import httpx
from langchain_core.callbacks import CallbackManagerForLLMRun, AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatResult, ChatGeneration

from .base import AgentRole, AIServiceProvider
from config.settings import get_settings

logger = logging.getLogger("kamal_express.providers.bitnet")


class ChatBitNet(BaseChatModel):
    """
    LangChain BaseChatModel wrapper for Alamia Connect BitNet endpoint.
    Supports bind_tools, prompt templates, and streaming.
    """
    server_url: str = "https://ai.alamiaconnect.com/v1"
    api_key: str = "51129693340"
    task: str = "dialogue"
    timeout_seconds: float = 60.0
    temperature: float = 0.7

    @property
    def _llm_type(self) -> str:
        return "bitnet"

    def _convert_messages(self, messages: List[BaseMessage]) -> list[dict[str, Any]]:
        payload = []
        for m in messages:
            if isinstance(m, SystemMessage):
                role = "system"
            elif isinstance(m, AIMessage):
                role = "assistant"
            elif isinstance(m, HumanMessage):
                role = "user"
            else:
                role = "user"
            payload.append({"role": role, "content": str(m.content)})
        return payload

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        payload_messages = self._convert_messages(messages)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = self.server_url.rstrip("/") + "/chat"
        body = {
            "messages": payload_messages,
            "task": self.task,
            "temperature": kwargs.get("temperature", self.temperature),
        }

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
                content = data.get("message", {}).get("content", "")
                message = AIMessage(content=content)
                return ChatResult(generations=[ChatGeneration(message=message)])
        except Exception as e:
            logger.error("BitNet request failed: %s", e)
            raise

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        payload_messages = self._convert_messages(messages)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = self.server_url.rstrip("/") + "/chat"
        body = {
            "messages": payload_messages,
            "task": self.task,
            "temperature": kwargs.get("temperature", self.temperature),
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
                content = data.get("message", {}).get("content", "")
                message = AIMessage(content=content)
                return ChatResult(generations=[ChatGeneration(message=message)])
        except Exception as e:
            logger.error("BitNet async request failed: %s", e)
            raise

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        """Bind tool definitions to the chat model."""
        return self.bind(tools=tools, **kwargs)


class BitNetProvider(AIServiceProvider):
    """
    Alamia Connect BitNet cloud AI provider.
    """

    @property
    def name(self) -> str:
        return "bitnet"

    def _task_for_role(self, role: AgentRole) -> str:
        cfg = get_settings().bitnet
        role_map = {
            "orchestrator": cfg.orchestrator_task,
            "visa": cfg.visa_task,
            "appointments": cfg.appointments_task,
            "default": cfg.default_task,
        }
        return role_map.get(role, cfg.default_task)

    def get_llm(
        self,
        role: AgentRole = "default",
        *,
        temperature: float = 0.7,
        streaming: bool = True,
        **kwargs: Any,
    ) -> ChatBitNet:
        cfg = get_settings().bitnet
        task = self._task_for_role(role)
        return ChatBitNet(
            server_url=cfg.server_url,
            api_key=cfg.api_key,
            task=task,
            timeout_seconds=float(cfg.timeout_seconds),
            temperature=temperature,
            **kwargs,
        )
