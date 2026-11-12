from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Optional
from uuid import uuid4

import httpx

from agent_factory_server.config import get_settings

CancelCheck = Callable[[], Awaitable[bool]]


class OpenAICompatClient:
    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        default_model: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.openai_api_base).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.openai_api_key
        self.default_model = default_model or settings.openai_model

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _payload(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]],
        model: Optional[str],
        temperature: Optional[float],
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "stream": stream,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if stream:
            # OpenAI-compatible: ask final chunk to include usage when supported.
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> dict[str, Any]:
        payload = self._payload(
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature,
            stream=False,
        )
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            if resp.status_code >= 400:
                detail = resp.text[:800]
                raise RuntimeError(f"LLM HTTP {resp.status_code}: {detail}")
            return resp.json()

    async def chat_stream(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        should_cancel: Optional[CancelCheck] = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield progress events, then a final `{type: 'complete', message: ...}`.

        If ``should_cancel`` returns True mid-stream, the HTTP stream is closed and
        ``{type:'cancelled'}`` is yielded (no complete event).
        """
        payload = self._payload(
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature,
            stream=True,
        )

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_acc: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] | None = None
        cancelled = False

        bodies: list[dict[str, Any]] = [payload]
        # Fallback body without stream_options for gateways that reject it.
        fallback = dict(payload)
        fallback.pop("stream_options", None)
        if "stream_options" in payload:
            bodies.append(fallback)

        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=120.0) as client:
            for attempt, body in enumerate(bodies):
                try:
                    async with client.stream(
                        "POST",
                        f"{self.base_url}/chat/completions",
                        headers=self._headers(),
                        json=body,
                    ) as resp:
                        if resp.status_code >= 400:
                            detail = (await resp.aread()).decode(
                                "utf-8", errors="ignore"
                            )[:800]
                            err = RuntimeError(f"LLM HTTP {resp.status_code}: {detail}")
                            # Retry without stream_options when likely unsupported.
                            if (
                                attempt == 0
                                and len(bodies) > 1
                                and (
                                    "stream_options" in detail.lower()
                                    or resp.status_code in (400, 422)
                                )
                            ):
                                last_error = err
                                continue
                            raise err

                        async for line in resp.aiter_lines():
                            if should_cancel is not None and await should_cancel():
                                cancelled = True
                                try:
                                    await resp.aclose()
                                except Exception:  # noqa: BLE001
                                    pass
                                break
                            if not line or not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            chunk_usage = chunk.get("usage")
                            if isinstance(chunk_usage, dict) and chunk_usage:
                                usage = chunk_usage
                            choices = chunk.get("choices") or []
                            if not choices:
                                continue
                            choice = choices[0] if isinstance(choices[0], dict) else {}
                            delta = choice.get("delta") or {}

                            reasoning = delta.get("reasoning_content")
                            if reasoning:
                                reasoning_parts.append(str(reasoning))
                                yield {"type": "reasoning_delta", "delta": str(reasoning)}

                            content = delta.get("content")
                            if content:
                                content_parts.append(str(content))
                                yield {"type": "message_delta", "delta": str(content)}

                            for tc in delta.get("tool_calls") or []:
                                if not isinstance(tc, dict):
                                    continue
                                idx = int(tc.get("index") or 0)
                                slot = tool_acc.setdefault(
                                    idx,
                                    {
                                        "id": "",
                                        "type": "function",
                                        "function": {"name": "", "arguments": ""},
                                    },
                                )
                                if tc.get("id"):
                                    slot["id"] = str(tc["id"])
                                if tc.get("type"):
                                    slot["type"] = str(tc["type"])
                                fn = tc.get("function") or {}
                                if isinstance(fn, dict):
                                    if fn.get("name"):
                                        slot["function"]["name"] = str(fn["name"])
                                    if fn.get("arguments"):
                                        slot["function"]["arguments"] = (
                                            slot["function"].get("arguments") or ""
                                        ) + str(fn["arguments"])
                        if cancelled:
                            break
                        last_error = None
                        break
                except RuntimeError as exc:
                    last_error = exc
                    if attempt + 1 >= len(bodies):
                        raise
                    continue
            if cancelled:
                yield {"type": "cancelled"}
                return
            if last_error is not None:
                raise last_error

        tool_calls = [tool_acc[i] for i in sorted(tool_acc.keys())] if tool_acc else []
        for call in tool_calls:
            if not call.get("id"):
                call["id"] = f"call_{uuid4().hex[:12]}"

        content_text = "".join(content_parts) if content_parts else None
        reasoning_text = "".join(reasoning_parts) if reasoning_parts else None
        message: dict[str, Any] = {
            "role": "assistant",
            "content": content_text,
        }
        if tool_calls:
            message["tool_calls"] = tool_calls
        if reasoning_text:
            message["reasoning_content"] = reasoning_text

        complete: dict[str, Any] = {"type": "complete", "message": message}
        if usage:
            complete["usage"] = usage
        yield complete

    async def chat_stream_text(
        self,
        *,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[str]:
        async for event in self.chat_stream(
            messages=messages, tools=None, model=model, temperature=temperature
        ):
            if event.get("type") == "message_delta":
                yield str(event.get("delta") or "")
