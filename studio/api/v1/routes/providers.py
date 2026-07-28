"""供應商、模型與提示詞模板 API。

**安全關鍵檔案。** 所有供應商回應都經由 `ProviderService.to_read()` 產生，
確保金鑰內容不會流出系統。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from studio.core.deps import DbSession, Paging
from studio.core.security import AdminUser, CurrentUser
from studio.models.types import ModelCategory, PromptCategory, ProviderKind
from studio.schemas.common import ApiResponse, OkData, Page
from studio.schemas.provider import (
    ModelCreate,
    ModelRead,
    ModelUpdate,
    PromptTemplateCreate,
    PromptTemplateRead,
    PromptTemplateUpdate,
    ProviderCreate,
    ProviderRead,
    ProviderTestResult,
    ProviderUpdate,
)
from studio.services.provider import ProviderService

router = APIRouter()


# ── 供應商 ────────────────────────────────────────────────────────────────────


@router.get(
    "/providers",
    response_model=ApiResponse[Page[ProviderRead]],
    operation_id="listProviders",
    summary="列出供應商",
    tags=["providers"],
)
async def list_providers(
    db: DbSession,
    _user: AdminUser,
    paging: Paging,
    search: Annotated[str | None, Query(description="以名稱、說明做模糊搜尋")] = None,
    provider_type: Annotated[ProviderKind | None, Query(description="依協定類型過濾")] = None,
) -> ApiResponse[Page[ProviderRead]]:
    """分頁列出供應商。回應不含任何金鑰內容。"""

    service = ProviderService(db)
    items, total = await service.list_providers(
        offset=paging.offset, limit=paging.limit, search=search, provider_type=provider_type
    )
    return ApiResponse.ok(
        Page.build(
            [service.to_read(item) for item in items],
            page=paging.page,
            page_size=paging.page_size,
            total=total,
        )
    )


@router.get(
    "/providers/{provider_id}",
    response_model=ApiResponse[ProviderRead],
    operation_id="getProvider",
    summary="取得供應商",
    tags=["providers"],
)
async def get_provider(provider_id: str, db: DbSession, _user: AdminUser) -> ApiResponse[ProviderRead]:
    """取得單一供應商。回應不含任何金鑰內容。"""

    service = ProviderService(db)
    return ApiResponse.ok(service.to_read(await service.get_provider(provider_id)))


@router.post(
    "/providers",
    response_model=ApiResponse[ProviderRead],
    status_code=status.HTTP_201_CREATED,
    operation_id="createProvider",
    summary="建立供應商",
    tags=["providers"],
)
async def create_provider(payload: ProviderCreate, db: DbSession, _user: AdminUser) -> ApiResponse[ProviderRead]:
    """建立供應商。`api_key_env` 只接受環境變數名稱，不接受金鑰本身。"""

    service = ProviderService(db)
    return ApiResponse.ok(service.to_read(await service.create_provider(payload)))


@router.patch(
    "/providers/{provider_id}",
    response_model=ApiResponse[ProviderRead],
    operation_id="updateProvider",
    summary="更新供應商",
    tags=["providers"],
)
async def update_provider(
    provider_id: str,
    payload: ProviderUpdate,
    db: DbSession,
    _user: AdminUser,
) -> ApiResponse[ProviderRead]:
    """更新供應商；未傳入的欄位（含 `api_key_env`）保留原值。"""

    service = ProviderService(db)
    return ApiResponse.ok(service.to_read(await service.update_provider(provider_id, payload)))


@router.delete(
    "/providers/{provider_id}",
    response_model=ApiResponse[OkData],
    operation_id="deleteProvider",
    summary="刪除供應商",
    tags=["providers"],
)
async def delete_provider(provider_id: str, db: DbSession, _user: AdminUser) -> ApiResponse[OkData]:
    """刪除供應商及其模型設定。"""

    await ProviderService(db).delete_provider(provider_id)
    return ApiResponse.ok(OkData())


@router.post(
    "/providers/{provider_id}/test",
    response_model=ApiResponse[ProviderTestResult],
    operation_id="testProvider",
    summary="測試供應商設定",
    tags=["providers"],
)
async def test_provider(provider_id: str, db: DbSession, _user: AdminUser) -> ApiResponse[ProviderTestResult]:
    """檢查供應商設定是否就緒。回應只說明缺少什麼，不含金鑰內容。"""

    return ApiResponse.ok(await ProviderService(db).test_provider(provider_id))


# ── 模型 ──────────────────────────────────────────────────────────────────────


@router.get(
    "/models",
    response_model=ApiResponse[Page[ModelRead]],
    operation_id="listModels",
    summary="列出模型",
    tags=["models"],
)
async def list_models(
    db: DbSession,
    _user: CurrentUser,
    paging: Paging,
    search: Annotated[str | None, Query(description="以名稱、模型識別碼做模糊搜尋")] = None,
    provider_id: Annotated[str | None, Query(description="依供應商過濾")] = None,
    category: Annotated[ModelCategory | None, Query(description="依類別過濾")] = None,
) -> ApiResponse[Page[ModelRead]]:
    """分頁列出模型設定。"""

    service = ProviderService(db)
    items, total = await service.list_models(
        offset=paging.offset, limit=paging.limit, search=search, provider_id=provider_id, category=category
    )
    return ApiResponse.ok(
        Page.build(
            [service.model_to_read(item, provider_name=item.provider.name if item.provider else "") for item in items],
            page=paging.page,
            page_size=paging.page_size,
            total=total,
        )
    )


@router.get(
    "/models/{model_id}",
    response_model=ApiResponse[ModelRead],
    operation_id="getModel",
    summary="取得模型",
    tags=["models"],
)
async def get_model(model_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[ModelRead]:
    """取得單一模型設定。"""

    service = ProviderService(db)
    model = await service.get_model(model_id)
    return ApiResponse.ok(service.model_to_read(model, provider_name=model.provider.name if model.provider else ""))


@router.post(
    "/models",
    response_model=ApiResponse[ModelRead],
    status_code=status.HTTP_201_CREATED,
    operation_id="createModel",
    summary="建立模型",
    tags=["models"],
)
async def create_model(payload: ModelCreate, db: DbSession, _user: AdminUser) -> ApiResponse[ModelRead]:
    """建立模型設定。供應商不存在時回 404。"""

    service = ProviderService(db)
    return ApiResponse.ok(service.model_to_read(await service.create_model(payload)))


@router.patch(
    "/models/{model_id}",
    response_model=ApiResponse[ModelRead],
    operation_id="updateModel",
    summary="更新模型",
    tags=["models"],
)
async def update_model(
    model_id: str,
    payload: ModelUpdate,
    db: DbSession,
    _user: AdminUser,
) -> ApiResponse[ModelRead]:
    """更新模型設定；未傳入的欄位保留原值。"""

    service = ProviderService(db)
    return ApiResponse.ok(service.model_to_read(await service.update_model(model_id, payload)))


@router.delete(
    "/models/{model_id}",
    response_model=ApiResponse[OkData],
    operation_id="deleteModel",
    summary="刪除模型",
    tags=["models"],
)
async def delete_model(model_id: str, db: DbSession, _user: AdminUser) -> ApiResponse[OkData]:
    """刪除模型設定。"""

    await ProviderService(db).delete_model(model_id)
    return ApiResponse.ok(OkData())


# ── 提示詞模板 ────────────────────────────────────────────────────────────────


@router.get(
    "/prompt-templates",
    response_model=ApiResponse[Page[PromptTemplateRead]],
    operation_id="listPromptTemplates",
    summary="列出提示詞模板",
    tags=["prompt-templates"],
)
async def list_prompt_templates(
    db: DbSession,
    _user: CurrentUser,
    paging: Paging,
    search: Annotated[str | None, Query(description="以名稱、內容做模糊搜尋")] = None,
    category: Annotated[PromptCategory | None, Query(description="依類別過濾")] = None,
    project_id: Annotated[str | None, Query(description="依專案過濾；留空為全域模板")] = None,
) -> ApiResponse[Page[PromptTemplateRead]]:
    """分頁列出提示詞模板。"""

    items, total = await ProviderService(db).list_templates(
        offset=paging.offset, limit=paging.limit, search=search, category=category, project_id=project_id
    )
    return ApiResponse.ok(
        Page.build(
            [PromptTemplateRead.model_validate(item) for item in items],
            page=paging.page,
            page_size=paging.page_size,
            total=total,
        )
    )


@router.get(
    "/prompt-templates/{template_id}",
    response_model=ApiResponse[PromptTemplateRead],
    operation_id="getPromptTemplate",
    summary="取得提示詞模板",
    tags=["prompt-templates"],
)
async def get_prompt_template(template_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[PromptTemplateRead]:
    """取得單一提示詞模板。"""

    template = await ProviderService(db).get_template(template_id)
    return ApiResponse.ok(PromptTemplateRead.model_validate(template))


@router.post(
    "/prompt-templates",
    response_model=ApiResponse[PromptTemplateRead],
    status_code=status.HTTP_201_CREATED,
    operation_id="createPromptTemplate",
    summary="建立提示詞模板",
    tags=["prompt-templates"],
)
async def create_prompt_template(
    payload: PromptTemplateCreate,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[PromptTemplateRead]:
    """建立提示詞模板；設為預設時會取消同類別其他模板的預設標記。"""

    template = await ProviderService(db).create_template(payload)
    return ApiResponse.ok(PromptTemplateRead.model_validate(template))


@router.patch(
    "/prompt-templates/{template_id}",
    response_model=ApiResponse[PromptTemplateRead],
    operation_id="updatePromptTemplate",
    summary="更新提示詞模板",
    tags=["prompt-templates"],
)
async def update_prompt_template(
    template_id: str,
    payload: PromptTemplateUpdate,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[PromptTemplateRead]:
    """更新提示詞模板；未傳入的欄位保留原值。"""

    template = await ProviderService(db).update_template(template_id, payload)
    return ApiResponse.ok(PromptTemplateRead.model_validate(template))


@router.delete(
    "/prompt-templates/{template_id}",
    response_model=ApiResponse[OkData],
    operation_id="deletePromptTemplate",
    summary="刪除提示詞模板",
    tags=["prompt-templates"],
)
async def delete_prompt_template(template_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[OkData]:
    """刪除提示詞模板。"""

    await ProviderService(db).delete_template(template_id)
    return ApiResponse.ok(OkData())
