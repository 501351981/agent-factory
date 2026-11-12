from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from agent_factory_server.auth import AuthContext, require_api_key
from agent_factory_server.config import get_settings

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

ALLOWED_CONTENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}
MAX_BYTES = 2 * 1024 * 1024


class UploadOut(BaseModel):
    url: str
    filename: str
    content_type: str
    size: int


def uploads_root() -> Path:
    settings = get_settings()
    root = Path(settings.upload_dir)
    if not root.is_absolute():
        from agent_factory_server.config import ROOT_DIR

        root = ROOT_DIR / root
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.post("/logo", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
async def upload_logo(
    file: UploadFile = File(...),
    _auth: AuthContext = Depends(require_api_key),
) -> UploadOut:
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    ext = ALLOWED_CONTENT_TYPES.get(content_type)
    if not ext:
        raise HTTPException(
            status_code=400,
            detail="仅支持 png / jpg / webp / gif / svg 图片",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="图片不能超过 2MB")

    filename = f"{uuid.uuid4().hex}{ext}"
    logos_dir = uploads_root() / "logos"
    logos_dir.mkdir(parents=True, exist_ok=True)
    target = logos_dir / filename
    target.write_bytes(data)

    return UploadOut(
        url=f"/uploads/logos/{filename}",
        filename=filename,
        content_type=content_type,
        size=len(data),
    )
