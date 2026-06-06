"""LLM Client — DeepSeek API 接入"""
from __future__ import annotations
import json, logging
from typing import Optional
import httpx

logger = logging.getLogger(__name__)
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"


class LLMClient:
    def __init__(self, api_key: str, base_url: str = DEEPSEEK_BASE_URL,
                 model: str = DEEPSEEK_MODEL, timeout_s: int = 30):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = min(timeout_s, 8)

    async def chat(self, system: str, messages: list[dict],
                   response_format: Optional[dict] = None,
                   temperature: float = 0.3, max_tokens: int = 4096) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = {"model": self.model, "messages": [{"role": "system", "content": system}, *messages],
                "temperature": temperature, "max_tokens": max_tokens, "stream": False}
        if response_format:
            body["response_format"] = response_format
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s, trust_env=False) as client:
                resp = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"].strip()
                return text
        except httpx.HTTPStatusError as e:
            logger.error("[LLM] HTTP %d: %s", e.response.status_code, e.response.text[:200])
            raise
        except httpx.TimeoutException:
            logger.error("[LLM] timeout %ds", self.timeout_s)
            raise
        except Exception as e:
            logger.error("[LLM] error: %s", e)
            raise

    async def chat_json(self, system: str, messages: list[dict],
                        temperature: float = 0.1) -> dict:
        text = await self.chat(system=system, messages=messages,
                               response_format={"type": "json_object"}, temperature=temperature)
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0]
        return json.loads(text.strip())
