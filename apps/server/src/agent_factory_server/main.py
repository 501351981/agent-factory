from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agent_factory_server.api import (
    agents,
    auth_routes,
    chat,
    client_ws,
    memories,
    models,
    sessions,
    uploads,
    usage,
)
from agent_factory_server.api.uploads import uploads_root
from agent_factory_server.config import get_settings
from agent_factory_server.db import models as _db_models  # noqa: F401 — register tables
from agent_factory_server.db.session import init_db
from agent_factory_server.runtime.runner import run_supervisor


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    # Seal legacy plaintext model API keys (no-op when already enc:v1:).
    from agent_factory_server.db.session import SessionLocal
    from agent_factory_server.db import repo as db_repo

    async with SessionLocal() as session:
        sealed = await db_repo.reseal_plaintext_model_api_keys(session)
        if sealed:
            import logging

            logging.getLogger(__name__).info(
                "Sealed %s legacy plaintext model API key(s) at rest", sealed
            )
    # After process restart: heal orphan running rows, then resume idle queues.
    await run_supervisor.resume_idle_queues()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Agent工厂", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(models.router)
    app.include_router(agents.router)
    app.include_router(auth_routes.router)
    app.include_router(sessions.router)
    app.include_router(memories.router)
    app.include_router(usage.router)
    app.include_router(chat.router)
    app.include_router(client_ws.router)
    app.include_router(uploads.router)

    upload_root = uploads_root()
    app.mount("/uploads", StaticFiles(directory=str(upload_root)), name="uploads")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "agent-factory"}

    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "agent_factory_server.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )


if __name__ == "__main__":
    run()
