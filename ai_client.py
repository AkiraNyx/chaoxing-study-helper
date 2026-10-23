"""极简 OpenAI 兼容客户端，默认指向 DeepSeek。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class AIError(Exception):
    pass


class AIClient:
    def __init__(self, base_url: str = "https://api.deepseek.com", api_key: str = "",
                 model: str = "deepseek-chat", timeout: int = 90,
                 reasoning_effort: str = ""):
        self.base_url = (base_url or "https://api.deepseek.com").rstrip("/")
        self.api_key = api_key
        self.model = model or "deepseek-chat"
        self.timeout = timeout
        self.reasoning_effort = reasoning_effort

    def chat(self, messages: list[dict], temperature: float = 0.2) -> str:
        if not self.api_key:
            raise AIError("还没有填写 API Key")
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "ChaoxingHelper/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise AIError(f"接口返回 HTTP {exc.code}：{detail[:300]}") from exc
        except urllib.error.URLError as exc:
            raise AIError(f"网络错误：{exc.reason}") from exc
        try:
            data = json.loads(body)
            return data["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise AIError(f"响应解析失败：{body[:300]}") from exc

    def test(self) -> str:
        reply = self.chat(
            [{"role": "user", "content": "只回复两个字：正常"}],
            temperature=0,
        )
        return reply.strip()[:50]

    def list_models(self) -> list[str]:
        if not self.api_key:
            raise AIError("还没有填写 API Key")
        request = urllib.request.Request(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.api_key}", "User-Agent": "ChaoxingHelper/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise AIError(f"接口返回 HTTP {exc.code}：{detail[:300]}") from exc
        except urllib.error.URLError as exc:
            raise AIError(f"网络错误：{exc.reason}") from exc
        try:
            data = json.loads(body)
            items = data.get("data") or []
            return [str(item["id"]) for item in items if item.get("id")]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise AIError("模型列表解析失败") from exc
