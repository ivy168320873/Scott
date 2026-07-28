"""供應商、模型與提示詞模板 Service。

**安全關鍵檔案。**

金鑰處理規則：
1. 資料庫只存 `api_key_env`（環境變數名稱），永不存金鑰值。
2. 更新時 `api_key_env` 未傳入 → 保留原值；只有明確傳入才更新。
3. 對外表示由 `to_read()` 產生，只附加 `api_key_configured` 布林，
   不回傳任何金鑰內容。
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from studio.config import secret_is_configured
from studio.core import ids
from studio.core.errors import ValidationError
from studio.models.provider import Model, PromptTemplate, Provider
from studio.models.types import ModelCategory, PromptCategory, ProviderKind, ProviderStatus
from studio.repositories import (
    ModelRepository,
    PromptTemplateRepository,
    ProviderRepository,
)
from studio.schemas.provider import (
    ModelCreate,
    ModelRead,
    ModelUpdate,
    PromptTemplateCreate,
    PromptTemplateUpdate,
    ProviderCreate,
    ProviderRead,
    ProviderTestResult,
    ProviderUpdate,
)
from studio.services.base import ensure_found, translate_integrity_error


class ProviderService:
    """供應商與模型業務邏輯。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.providers = ProviderRepository(session)
        self.models = ModelRepository(session)
        self.templates = PromptTemplateRepository(session)

    # ── 對外表示（安全邊界）──────────────────────────────────────────────────

    @staticmethod
    def to_read(provider: Provider) -> ProviderRead:
        """把 Provider 轉為對外表示。

        這是唯一允許把 Provider 送出系統的路徑。
        `api_key_configured` 只透露該環境變數「有沒有設定」，不透露內容；
        Provider 模型本身也沒有任何欄位存放金鑰值。
        """

        return ProviderRead(
            id=provider.id,
            name=provider.name,
            provider_type=provider.kind,
            base_url=provider.base_url,
            image_base_url=provider.image_base_url,
            video_base_url=provider.video_base_url,
            enabled=provider.status is ProviderStatus.active,
            api_key_env=provider.api_key_env,
            api_key_configured=secret_is_configured(provider.api_key_env),
            description=provider.description,
            timeout_seconds=provider.timeout_seconds,
            max_retries=provider.max_retries,
            created_at=provider.created_at,
            updated_at=provider.updated_at,
        )

    @staticmethod
    def model_to_read(model: Model, *, provider_name: str = "") -> ModelRead:
        """把 Model 轉為對外表示。"""

        return ModelRead(
            id=model.id,
            provider_id=model.provider_id,
            provider_name=provider_name,
            name=model.name,
            model_id=model.model_id,
            category=model.category,
            enabled=bool(model.enabled),
            params=model.params,
            description=model.description,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    # ── 供應商 ────────────────────────────────────────────────────────────────

    async def list_providers(
        self,
        *,
        offset: int,
        limit: int,
        search: str | None = None,
        provider_type: ProviderKind | None = None,
    ) -> tuple[list[Provider], int]:
        """分頁列出供應商。"""

        return await self.providers.list_page(offset=offset, limit=limit, search=search, kind=provider_type)

    async def get_provider(self, provider_id: str) -> Provider:
        """取得供應商，不存在時拋出 404。"""

        return ensure_found(await self.providers.get(provider_id), resource="供應商", entity_id=provider_id)

    async def create_provider(self, payload: ProviderCreate) -> Provider:
        """建立供應商。"""

        provider = Provider(
            id=ids.new_id(ids.PROVIDER),
            name=payload.name,
            kind=payload.provider_type,
            api_key_env=payload.api_key_env,
            base_url=payload.base_url,
            image_base_url=payload.image_base_url,
            video_base_url=payload.video_base_url,
            status=ProviderStatus.active if payload.enabled else ProviderStatus.testing,
            description=payload.description,
            timeout_seconds=payload.timeout_seconds,
            max_retries=payload.max_retries,
            extra_config=payload.extra_config,
        )
        try:
            return await self.providers.add(provider)
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="供應商") from exc

    async def update_provider(self, provider_id: str, payload: ProviderUpdate) -> Provider:
        """更新供應商。

        **金鑰保留規則**：`api_key_env` 未出現在請求中時保留原值。
        使用 `exclude_unset` 而非檢查 None —— 前端送 `null` 與「不送」
        是兩件事，前者才代表要清空。
        """

        provider = await self.get_provider(provider_id)
        data = payload.model_dump(exclude_unset=True)

        if "provider_type" in data:
            provider.kind = data.pop("provider_type")
        if "enabled" in data:
            enabled = data.pop("enabled")
            provider.status = ProviderStatus.active if enabled else ProviderStatus.disabled

        # api_key_env 只有在明確傳入時才更新；未傳入則保留舊值。
        if "api_key_env" in data and data["api_key_env"] is None:
            data.pop("api_key_env")

        for field, value in data.items():
            if value is not None and hasattr(provider, field):
                setattr(provider, field, value)

        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="供應商") from exc
        return provider

    async def delete_provider(self, provider_id: str) -> None:
        """刪除供應商（其模型由外鍵級聯刪除）。"""

        await self.providers.remove(await self.get_provider(provider_id))

    async def test_provider(self, provider_id: str) -> ProviderTestResult:
        """測試供應商設定是否就緒。

        Phase 3 只做**設定完整性檢查**（金鑰環境變數是否已設定、base_url 是否填寫），
        不發出真實網路請求 —— 實際連線測試在 Phase 5 接上 Adapter 後才有意義。
        回傳訊息刻意只說明缺少什麼，不含任何金鑰內容。
        """

        provider = await self.get_provider(provider_id)

        problems: list[str] = []
        if not provider.api_key_env:
            problems.append("尚未設定 api_key_env")
        elif not secret_is_configured(provider.api_key_env):
            problems.append(f"環境變數 {provider.api_key_env} 未設定")
        if not provider.base_url:
            problems.append("尚未設定 base_url")

        return ProviderTestResult(
            provider_id=provider_id,
            ok=not problems,
            detail="設定就緒" if not problems else "；".join(problems),
        )

    # ── 模型 ──────────────────────────────────────────────────────────────────

    async def list_models(
        self,
        *,
        offset: int,
        limit: int,
        search: str | None = None,
        provider_id: str | None = None,
        category: ModelCategory | None = None,
    ) -> tuple[list[Model], int]:
        """分頁列出模型設定。"""

        return await self.models.list_page(
            offset=offset, limit=limit, search=search, provider_id=provider_id, category=category
        )

    async def get_model(self, model_id: str) -> Model:
        """取得模型設定，不存在時拋出 404。"""

        return ensure_found(await self.models.get(model_id), resource="模型", entity_id=model_id)

    async def create_model(self, payload: ModelCreate) -> Model:
        """建立模型設定。

        先驗證供應商存在，讓錯誤是清楚的 404 而非資料庫外鍵錯誤。
        """

        await self.get_provider(payload.provider_id)

        model = Model(
            id=ids.new_id(ids.MODEL),
            provider_id=payload.provider_id,
            name=payload.name,
            model_id=payload.model_id,
            category=payload.category,
            enabled=1 if payload.enabled else 0,
            params=payload.params,
            description=payload.description,
        )
        try:
            return await self.models.add(model)
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="模型") from exc

    async def update_model(self, model_id: str, payload: ModelUpdate) -> Model:
        """更新模型設定；未傳入的欄位保留原值。"""

        model = await self.get_model(model_id)
        data = payload.model_dump(exclude_unset=True)

        if "enabled" in data and data["enabled"] is not None:
            model.enabled = 1 if data.pop("enabled") else 0
        else:
            data.pop("enabled", None)

        for field, value in data.items():
            if value is not None and hasattr(model, field):
                setattr(model, field, value)

        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="模型") from exc
        return model

    async def delete_model(self, model_id: str) -> None:
        """刪除模型設定。"""

        await self.models.remove(await self.get_model(model_id))

    # ── 提示詞模板 ────────────────────────────────────────────────────────────

    async def list_templates(
        self,
        *,
        offset: int,
        limit: int,
        search: str | None = None,
        category: PromptCategory | None = None,
        project_id: str | None = None,
    ) -> tuple[list[PromptTemplate], int]:
        """分頁列出提示詞模板。"""

        return await self.templates.list_page(
            offset=offset, limit=limit, search=search, category=category, project_id=project_id
        )

    async def get_template(self, template_id: str) -> PromptTemplate:
        """取得模板，不存在時拋出 404。"""

        return ensure_found(await self.templates.get(template_id), resource="提示詞模板", entity_id=template_id)

    async def create_template(self, payload: PromptTemplateCreate) -> PromptTemplate:
        """建立提示詞模板。"""

        template = PromptTemplate(
            id=ids.new_id(ids.PROMPT_TEMPLATE),
            category=payload.category,
            name=payload.name,
            content=payload.content,
            description=payload.description,
            is_default=1 if payload.is_default else 0,
            project_id=payload.project_id,
            variables=payload.variables,
        )
        if payload.is_default:
            await self._clear_default(payload.category, payload.project_id)

        try:
            return await self.templates.add(template)
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="提示詞模板") from exc

    async def update_template(self, template_id: str, payload: PromptTemplateUpdate) -> PromptTemplate:
        """更新提示詞模板；未傳入的欄位保留原值。"""

        template = await self.get_template(template_id)
        data = payload.model_dump(exclude_unset=True)

        if data.get("is_default"):
            await self._clear_default(data.get("category", template.category), template.project_id)
            template.is_default = 1
        elif "is_default" in data:
            template.is_default = 0
        data.pop("is_default", None)

        for field, value in data.items():
            if value is not None and hasattr(template, field):
                setattr(template, field, value)

        await self.session.flush()
        return template

    async def delete_template(self, template_id: str) -> None:
        """刪除提示詞模板。"""

        await self.templates.remove(await self.get_template(template_id))

    async def _clear_default(self, category: PromptCategory, project_id: str | None) -> None:
        """取消同類別既有的預設標記。

        「預設」在同一範圍內必須唯一，否則組提示詞時無從選擇。
        """

        existing, _ = await self.templates.list_page(
            offset=0, limit=100, category=category, project_id=project_id, is_default=1
        )
        for item in existing:
            item.is_default = 0
        await self.session.flush()

    async def resolve_template(self, category: PromptCategory, *, project_id: str | None = None) -> PromptTemplate:
        """解析某類別應使用的模板。

        優先順序：專案級預設 → 全域預設。兩者皆無時拋出 404，
        讓呼叫端明確知道缺少設定，而不是靜默使用空字串。
        """

        if project_id:
            scoped, _ = await self.templates.list_page(
                offset=0, limit=1, category=category, project_id=project_id, is_default=1
            )
            if scoped:
                return scoped[0]

        globals_, _ = await self.templates.list_page(
            offset=0, limit=1, category=category, project_id=None, is_default=1
        )
        if globals_:
            return globals_[0]

        raise ValidationError(
            f"類別 {category.value} 尚未設定預設提示詞模板",
            details={"category": category.value},
        )
