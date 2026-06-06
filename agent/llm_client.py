"""
LLM Client — 多模型支持（DeepSeek + Anthropic Claude）
来自 V2 的多 Provider 架构
"""
from __future__ import annotations
import json, logging, os
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"


def _provider() -> str:
    """返回可用 provider: deepseek / anthropic / none"""
    if os.environ.get(DEEPSEEK_API_KEY_ENV):
        return "deepseek"
    if os.environ.get(ANTHROPIC_API_KEY_ENV):
        return "anthropic"
    return "none"


def _has_api_key() -> bool:
    return _provider() != "none"


class LLMClient:
    def __init__(self, api_key: str = "", base_url: str = DEEPSEEK_BASE_URL,
                 model: str = DEEPSEEK_MODEL, timeout_s: int = 30):
        self.api_key = api_key or os.environ.get(DEEPSEEK_API_KEY_ENV, "")
        self.anthropic_key = os.environ.get(ANTHROPIC_API_KEY_ENV, "")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s

    @property
    def provider(self) -> str:
        if self.api_key:
            return "deepseek"
        if self.anthropic_key:
            return "anthropic"
        return "none"

    async def chat(self, system: str, messages: list[dict],
                   response_format: Optional[dict] = None,
                   temperature: float = 0.3, max_tokens: int = 4096) -> str:
        if self.provider == "deepseek":
            return await self._chat_deepseek(system, messages, response_format,
                                              temperature, max_tokens)
        elif self.provider == "anthropic":
            return await self._chat_anthropic(system, messages, temperature, max_tokens)
        else:
            logger.error("[LLM] 未配置任何 API Key")
            raise ValueError("未配置 LLM API Key（需设置 DEEPSEEK_API_KEY 或 ANTHROPIC_API_KEY）")

    async def chat_json(self, system: str, messages: list[dict],
                        temperature: float = 0.1) -> dict:
        text = await self.chat(system=system, messages=messages,
                               response_format={"type": "json_object"},
                               temperature=temperature)
        text = text.strip()
        # 去除 markdown 代码块
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0]
            text = text.strip()
        # 容错补全 {}
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start:end+1]
            else:
                text = "{" + text + "}"
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            fixed = text.replace("'", '"').replace("\n", " ").replace("\r", " ")
            try:
                return json.loads(fixed)
            except json.JSONDecodeError:
                logger.error("[LLM] JSON 解析失败: %.100s", text)
                return {"action": "chat", "response": "系统正忙，请重试"}

    # ── DeepSeek ──

    async def _chat_deepseek(self, system: str, messages: list[dict],
                              response_format: Optional[dict] = None,
                              temperature: float = 0.3,
                              max_tokens: int = 4096) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"}
        body = {"model": self.model,
                "messages": [{"role": "system", "content": system}, *messages],
                "temperature": temperature, "max_tokens": max_tokens, "stream": False}
        if response_format:
            body["response_format"] = response_format
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s, trust_env=False) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as e:
            logger.error("[DeepSeek] HTTP %d: %s",
                         e.response.status_code, e.response.text[:200])
            raise
        except httpx.TimeoutException:
            logger.error("[DeepSeek] timeout %ds", self.timeout_s)
            raise

    # ── Anthropic ──

    async def _chat_anthropic(self, system: str, messages: list[dict],
                               temperature: float = 0.3,
                               max_tokens: int = 4096) -> str:
        headers = {
            "x-api-key": self.anthropic_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        # 转换消息格式
        claude_messages = []
        for m in messages:
            role = "user" if m.get("role") in ("user", "system") else "assistant"
            claude_messages.append({"role": role, "content": m.get("content", "")})
        body = {
            "model": "claude-sonnet-4-20250514",
            "system": system,
            "messages": claude_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s, trust_env=False) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
                return data["content"][0]["text"].strip()
        except httpx.HTTPStatusError as e:
            logger.error("[Anthropic] HTTP %d: %s",
                         e.response.status_code, e.response.text[:200])
            raise
        except httpx.TimeoutException:
            logger.error("[Anthropic] timeout %ds", self.timeout_s)
            raise
