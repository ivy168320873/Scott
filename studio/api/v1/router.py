"""Studio API v1 路由匯總。

各資源 router 在此集中掛載；`studio.main` 只需掛這一個 router，
新增資源時不必修改 app 組裝邏輯。
"""

from __future__ import annotations

from fastapi import APIRouter

from studio.api.v1.routes import assets, health, media, projects, providers, shots, tasks

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(projects.router)
api_router.include_router(shots.router)
api_router.include_router(assets.character_router)
api_router.include_router(assets.actor_router)
api_router.include_router(assets.scene_router)
api_router.include_router(assets.prop_router)
api_router.include_router(assets.costume_router)
api_router.include_router(media.router)
api_router.include_router(providers.router)
api_router.include_router(tasks.router)
