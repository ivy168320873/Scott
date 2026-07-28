"""Studio API v1 路由匯總。

各資源 router 在此集中掛載；`studio.main` 只需掛這一個 router，
新增資源時不必修改 app 組裝邏輯。
"""

from __future__ import annotations

from fastapi import APIRouter

from studio.api.v1.routes import health

api_router = APIRouter()

api_router.include_router(health.router)
