"""DeepSeek Chat Completions: bounded retries and validated text/JSON results."""

import asyncio
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Literal, Sequence

import httpx

from chessmuse.llm.config import DeepSeekSettings


class LLMError(RuntimeError):
    """Public, sanitized error; no raw server body, request or API key attached."""

    def __init__(self, message: str, *, kind: str, status_code: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str

    def __post_init__(self):
        if self.role not in {"system", "user", "assistant"} or not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("Message requires a supported role and nonempty text")


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    request_id: str
    usage: TokenUsage
    # JSON mode guarantees syntax here, not the truth of chess commentary.
    data: dict | None = field(default=None)


def retry_delay(attempt: int, retry_after: str | None = None) -> float:
    """Respect seconds or HTTP-date Retry-After, bounded to 30 seconds."""
    if retry_after:
        try:
            seconds = float(retry_after)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                seconds = math.nan
        if math.isfinite(seconds):
            return max(0, min(seconds, 30))
    return min(2 ** attempt, 30)


class DeepSeekClient:
    """Use as an async context manager; inject an HTTP transport for offline tests."""

    def __init__(self, settings: DeepSeekSettings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {settings.api_key}"},
            timeout=httpx.Timeout(settings.timeout_seconds, connect=min(10, settings.timeout_seconds)),
            transport=transport,
            follow_redirects=False,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self):
        await self._http.aclose()

    async def complete(self, messages: Sequence[Message], *, json_mode: bool = False,
                       max_tokens: int | None = None, temperature: float = 0.3) -> Completion:
        if not messages or any(not isinstance(message, Message) for message in messages):
            raise ValueError("Provide at least one Message")
        tokens = self.settings.max_tokens if max_tokens is None else max_tokens
        if not isinstance(tokens, int) or not 1 <= tokens <= 393216:
            raise ValueError("max_tokens must be between 1 and 393216")
        if not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        request_messages = [asdict(message) for message in messages]
        if json_mode:
            request_messages.insert(0, {"role": "system", "content": "Return a single valid JSON object. Do not use Markdown fences."})
        payload = {
            "model": self.settings.model, "messages": request_messages,
            "thinking": {"type": self.settings.thinking},
            "stream": False, "max_tokens": tokens, "temperature": temperature,
            "response_format": {"type": "json_object" if json_mode else "text"},
        }
        response = await self._request(payload)
        return self._parse(response, json_mode=json_mode)

    async def _request(self, payload: dict) -> httpx.Response:
        for attempt in range(self.settings.max_retries + 1):
            try:
                async with asyncio.timeout(self.settings.timeout_seconds):
                    response = await self._http.post("chat/completions", json=payload)
            except (httpx.TransportError, TimeoutError):
                if attempt < self.settings.max_retries:
                    await asyncio.sleep(retry_delay(attempt))
                    continue
                raise LLMError("DeepSeek 网络请求失败或超时，请检查连接后重试", kind="network") from None
            if response.is_success:
                return response
            status = response.status_code
            if (status == 429 or 500 <= status < 600) and attempt < self.settings.max_retries:
                await asyncio.sleep(retry_delay(attempt, response.headers.get("Retry-After")))
                continue
            hints = {
                400: "请求参数无效，请检查模型和上下文长度",
                401: "认证失败，请检查 DEEPSEEK_API_KEY",
                402: "账户余额不足",
                403: "当前账户无权执行此请求",
                404: "接口地址或模型不可用",
                429: "请求限流，请稍后重试",
            }
            hint = hints.get(status, "服务请求失败，请稍后重试")
            raise LLMError(f"DeepSeek HTTP {status}：{hint}", kind="http", status_code=status)
        raise AssertionError("Unreachable retry state")

    @staticmethod
    def _parse(response: httpx.Response, *, json_mode: bool) -> Completion:
        try:
            body = response.json()
            choice = body["choices"][0]
            reason = choice["finish_reason"]
            text = choice["message"]["content"]
            model, request_id = body["model"], body["id"]
            usage = body["usage"]
            counts = [usage[name] for name in ("prompt_tokens", "completion_tokens", "total_tokens")]
            if not all(type(n) is int and n >= 0 for n in counts):
                raise ValueError
            if not isinstance(model, str) or not isinstance(request_id, str):
                raise ValueError
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("DeepSeek 返回了无效的响应结构", kind="response") from None
        if reason != "stop":
            hint = "输出被截断，请增加 max_tokens 或缩短输入" if reason == "length" else "模型未正常完成输出"
            raise LLMError(f"DeepSeek {hint}", kind="incomplete")
        if not isinstance(text, str) or not text.strip():
            raise LLMError("DeepSeek 返回了空内容", kind="response")
        data = None
        if json_mode:
            try:
                data = json.loads(text)
                if not isinstance(data, dict):
                    raise ValueError
            except ValueError:
                raise LLMError("DeepSeek 未返回有效的 JSON 对象", kind="json") from None
        return Completion(text=text, model=model, request_id=request_id,
                          usage=TokenUsage(*counts), data=data)
