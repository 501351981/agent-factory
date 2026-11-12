from __future__ import annotations

from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from agent_factory_server.auth import AuthContext, require_api_key, require_caller
from agent_factory_server.db import repo
from agent_factory_server.db.session import get_session
from agent_factory_server.domain.schemas import (
    DiscoverModelsOut,
    DiscoverModelsRequest,
    ModelProfileCreate,
    ModelProfileOut,
    ModelProfileUpdate,
    RemoteModelInfo,
)
from agent_factory_server.llm.openai_compat import OpenAICompatClient

router = APIRouter(prefix="/api/models", tags=["models"])

PROVIDER_PRESETS = [
    {
        "provider": "deepseek",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model_name": "deepseek-chat",
        "context_window_tokens": 128000,
        "reserve_output_tokens": 8192,
    },
    {
        "provider": "zhipu",
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model_name": "glm-4.5",
        "context_window_tokens": 128000,
        "reserve_output_tokens": 8192,
    },
    {
        "provider": "openai",
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model_name": "gpt-4o-mini",
        "context_window_tokens": 128000,
        "reserve_output_tokens": 16384,
    },
    {
        "provider": "moonshot",
        "label": "Moonshot 月之暗面",
        "base_url": "https://api.moonshot.cn/v1",
        "model_name": "moonshot-v1-8k",
        "context_window_tokens": 8192,
        "reserve_output_tokens": 2048,
    },
    {
        "provider": "qwen",
        "label": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_name": "qwen-plus",
        "context_window_tokens": 131072,
        "reserve_output_tokens": 8192,
    },
    {
        "provider": "custom",
        "label": "自定义 OpenAI 兼容",
        "base_url": "https://example.com/v1",
        "model_name": "my-model",
        "context_window_tokens": 128000,
        "reserve_output_tokens": 8192,
    },
]

_CONTEXT_KEYS = (
    "max_model_len",
    "context_length",
    "context_window",
    "max_context_length",
    "context_window_tokens",
    "max_input_tokens",
)


def _coerce_positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.isdigit():
            num = int(cleaned)
            return num if num > 0 else None
    return None


def _extract_context_window(item: dict[str, Any]) -> Optional[int]:
    for key in _CONTEXT_KEYS:
        hit = _coerce_positive_int(item.get(key))
        if hit is not None:
            return hit
    for nest_key in ("meta", "model_info", "capabilities", "sogni", "limits"):
        nested = item.get(nest_key)
        if not isinstance(nested, dict):
            continue
        for key in _CONTEXT_KEYS:
            hit = _coerce_positive_int(nested.get(key))
            if hit is not None:
                return hit
        usable = _coerce_positive_int(nested.get("usable_context_length"))
        if usable is not None:
            return usable
    return None


@router.get("/presets")
async def list_presets(
    _auth: AuthContext = Depends(require_api_key),
) -> list[dict]:
    return PROVIDER_PRESETS


@router.post("/discover", response_model=DiscoverModelsOut)
async def discover_models(
    payload: DiscoverModelsRequest,
    session: AsyncSession = Depends(get_session),
    _auth: AuthContext = Depends(require_api_key),
) -> DiscoverModelsOut:
    """Best-effort probe of upstream GET /models (no universal context field)."""
    base_url = (payload.base_url or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(status_code=400, detail="base_url is required")

    api_key = (payload.api_key or "").strip()
    if not api_key and payload.profile_id:
        row = await repo.get_model_profile(session, payload.profile_id)
        if not row:
            raise HTTPException(status_code=404, detail="Model profile not found")
        try:
            api_key = repo.reveal_profile_api_key(row)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{base_url}/models", headers=headers)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"无法连接上游 /models：{exc}") from exc

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=400,
            detail=f"上游 /models HTTP {resp.status_code}: {resp.text[:400]}",
        )

    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"上游 /models 返回非 JSON：{exc}") from exc

    raw_list: list[Any]
    if isinstance(body, dict):
        data = body.get("data")
        raw_list = data if isinstance(data, list) else []
    elif isinstance(body, list):
        raw_list = body
    else:
        raw_list = []

    models: list[RemoteModelInfo] = []
    with_ctx = 0
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or item.get("model") or "").strip()
        if not model_id:
            continue
        ctx = _extract_context_window(item)
        if ctx is not None:
            with_ctx += 1
        owned = item.get("owned_by")
        models.append(
            RemoteModelInfo(
                id=model_id,
                context_window_tokens=ctx,
                owned_by=str(owned) if owned is not None else None,
                raw_keys=sorted(str(k) for k in item.keys()),
            )
        )

    if not models:
        note = "上游 /models 无可用模型条目；请手动填写模型名与上下文窗口。"
    elif with_ctx == 0:
        note = (
            f"已拉取 {len(models)} 个模型，但未返回上下文窗口字段"
            "（OpenAI 兼容接口不保证提供）。请按厂商文档填写，或选用快捷模板默认值。"
        )
    else:
        note = f"已拉取 {len(models)} 个模型，其中 {with_ctx} 个带有上下文窗口信息。"

    return DiscoverModelsOut(models=models, note=note)


@router.get("", response_model=list[ModelProfileOut])
async def list_models(
    session: AsyncSession = Depends(get_session),
    _auth: AuthContext = Depends(require_caller),
) -> list[ModelProfileOut]:
    return await repo.list_model_profiles(session)


@router.post("", response_model=ModelProfileOut, status_code=status.HTTP_201_CREATED)
async def create_model(
    payload: ModelProfileCreate,
    session: AsyncSession = Depends(get_session),
    _auth: AuthContext = Depends(require_api_key),
) -> ModelProfileOut:
    return await repo.create_model_profile(session, payload)


@router.get("/{profile_id}", response_model=ModelProfileOut)
async def get_model(
    profile_id: str,
    session: AsyncSession = Depends(get_session),
    _auth: AuthContext = Depends(require_caller),
) -> ModelProfileOut:
    row = await repo.get_model_profile(session, profile_id)
    if not row:
        raise HTTPException(status_code=404, detail="Model profile not found")
    return repo.model_to_out(row)


@router.patch("/{profile_id}", response_model=ModelProfileOut)
async def update_model(
    profile_id: str,
    payload: ModelProfileUpdate,
    session: AsyncSession = Depends(get_session),
    _auth: AuthContext = Depends(require_api_key),
) -> ModelProfileOut:
    updated = await repo.update_model_profile(session, profile_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Model profile not found")
    return updated


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    profile_id: str,
    session: AsyncSession = Depends(get_session),
    _auth: AuthContext = Depends(require_api_key),
) -> None:
    ok = await repo.delete_model_profile(session, profile_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Model profile not found")


@router.post("/{profile_id}/test")
async def test_model(
    profile_id: str,
    session: AsyncSession = Depends(get_session),
    _auth: AuthContext = Depends(require_api_key),
) -> dict[str, str]:
    row = await repo.get_model_profile(session, profile_id)
    if not row:
        raise HTTPException(status_code=404, detail="Model profile not found")
    try:
        api_key = repo.reveal_profile_api_key(row)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    client = OpenAICompatClient(
        base_url=row.base_url,
        api_key=api_key,
        default_model=row.model_name,
    )
    try:
        completion = await client.chat(
            messages=[{"role": "user", "content": "请只回复：ok"}],
            model=row.model_name,
            temperature=float(row.temperature if row.temperature is not None else 0.3),
        )
        content = ((completion.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return {"status": "ok", "message": content[:200] or "调用成功"}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
