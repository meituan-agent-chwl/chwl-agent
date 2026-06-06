"""
LLM Client — 多 Provider 支持（DeepSeek / Anthropic Claude / LongCat）
OpenAI 兼容格式 + Anthropic 原生格式
"""
from __future__ import annotations
import json, logging, os
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

# Provider 环境变量
ENV_PROVIDER     = "LLM_PROVIDER"          # longcat | deepseek | anthropic
ENV_DEEPSEEK_KEY = "DEEPSEEK_API_KEY"
ENV_ANTHROPIC_KEY= "ANTHROPIC_API_KEY"
ENV_LONGCAT_KEY  = "LONGCAT_API_KEY"
ENV_LONGCAT_URL  = "LONGCAT_BASE_URL"      # https://api.longcat.chat/openai
ENV_LONGCAT_MODEL= "LONGCAT_MODEL"

DEFAULT_LONGCAT_URL   = "https://api.longcat.chat/openai/v1"
DEFAULT_LONGCAT_MODEL = "LongCat-2.0-Preview"


def _provider() -> str:
    """返回可用 provider: longcat / deepseek / anthropic / none"""
    override = os.environ.get(ENV_PROVIDER, "").lower().strip()
    if override:
        if override == "longcat" and os.environ.get(ENV_LONGCAT_KEY):
            return "longcat"
        if override == "deepseek" and os.environ.get(ENV_DEEPSEEK_KEY):
            return "deepseek"
        if override == "anthropic" and os.environ.get(ENV_ANTHROPIC_KEY):
            return "anthropic"
    # 自动检测
    if os.environ.get(ENV_LONGCAT_KEY):
        return "longcat"
    if os.environ.get(ENV_DEEPSEEK_KEY):
        return "deepseek"
    if os.environ.get(ENV_ANTHROPIC_KEY):
        return "anthropic"
    return "none"


class LLMClient:
    def __init__(self, api_key: str = "", base_url: str = "",
                 model: str = "", timeout_s: int = 30):
        # 如果传入了 api_key 但环境变量没设，补到环境变量里让 _provider() 能检测到
        if api_key:
            if not os.environ.get(ENV_LONGCAT_KEY) and not os.environ.get(ENV_DEEPSEEK_KEY):
                os.environ[ENV_LONGCAT_KEY] = api_key
            if not os.environ.get(ENV_DEEPSEEK_KEY):
                os.environ[ENV_DEEPSEEK_KEY] = api_key
        self.provider_name = _provider()
        # LongCat
        self.longcat_key = os.environ.get(ENV_LONGCAT_KEY, api_key)
        self.longcat_url = (os.environ.get(ENV_LONGCAT_URL, DEFAULT_LONGCAT_URL)).rstrip("/")
        self.longcat_model = os.environ.get(ENV_LONGCAT_MODEL, DEFAULT_LONGCAT_MODEL)
        # DeepSeek
        self.deepseek_key = os.environ.get(ENV_DEEPSEEK_KEY, api_key)
        self.deepseek_url = base_url or "https://api.deepseek.com/v1"
        self.deepseek_model = model or "deepseek-chat"
        # Anthropic
        self.anthropic_key = os.environ.get(ENV_ANTHROPIC_KEY, "")
        self.timeout_s = timeout_s

    async def chat(self, system: str, messages: list[dict],
                   response_format: Optional[dict] = None,
                   temperature: float = 0.3, max_tokens: int = 4096) -> str:
        if self.provider_name in ("longcat", "deepseek"):
            return await self._chat_openai(
                api_key=self.longcat_key if self.provider_name == "longcat" else self.deepseek_key,
                base_url=self.longcat_url if self.provider_name == "longcat" else self.deepseek_url,
                model=self.longcat_model if self.provider_name == "longcat" else self.deepseek_model,
                system=system, messages=messages,
                response_format=response_format,
                temperature=temperature, max_tokens=max_tokens,
            )
        elif self.provider_name == "anthropic":
            return await self._chat_anthropic(system, messages, temperature, max_tokens)
        else:
            raise ValueError("未配置 LLM API Key（需设置 LONGCAT_API_KEY / DEEPSEEK_API_KEY / ANTHROPIC_API_KEY）")

    async def chat_json(self, system: str, messages: list[dict],
                        temperature: float = 0.1) -> dict:
        text = await self.chat(system=system, messages=messages,
                               response_format={"type": "json_object"},
                               temperature=temperature)
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0]
            text = text.strip()
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
                return {"action": "chat", "response": f"[JSON解析失败] {text[:80]}"}

    # ── OpenAI 兼容格式（LongCat / DeepSeek） ──

    async def _chat_openai(self, api_key: str, base_url: str, model: str,
                            system: str, messages: list[dict],
                            response_format: Optional[dict] = None,
                            temperature: float = 0.3,
                            max_tokens: int = 4096) -> str:
        headers = {"Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"}
        body = {"model": model,
                "messages": [{"role": "system", "content": system}, *messages],
                "temperature": temperature, "max_tokens": max_tokens, "stream": False}
        if response_format:
            body["response_format"] = response_format
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s, trust_env=False) as client:
                resp = await client.post(
                    f"{base_url.rstrip('/')}/chat/completions",
                    headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as e:
            logger.error("[OpenAI] HTTP %d: %s", e.response.status_code, e.response.text[:200])
            raise
        except httpx.TimeoutException:
            logger.error("[OpenAI] timeout %ds", self.timeout_s)
            raise

    # ── Anthropic Claude ──

    async def _chat_anthropic(self, system: str, messages: list[dict],
                               temperature: float = 0.3,
                               max_tokens: int = 4096) -> str:
        headers = {
            "x-api-key": self.anthropic_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
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
            logger.error("[Anthropic] HTTP %d: %s", e.response.status_code, e.response.text[:200])
            raise
        except httpx.TimeoutException:
            logger.error("[Anthropic] timeout %ds", self.timeout_s)
            raise
