"""任務執行期的供應商／模型解析。

規則：任務指定的模型 > 全域預設模型 > 該類別中唯一啟用的模型。
解析失敗時拋出 `ConfigurationError`，讓任務以明確的「設定缺失」失敗，
而不是在呼叫供應商時才拋出難以理解的錯誤。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from studio.contracts.generation import ProviderConfig
from studio.core.db import session_scope
from studio.core.errors import ConfigurationError
from studio.models.provider import Model, ModelSettings, Provider
from studio.models.types import ModelCategory


@dataclass(slots=True)
class ResolvedModel:
    """解析後的模型與供應商設定。"""

    model_name: str
    provider_kind: str
    config: ProviderConfig
    model_id: str
    provider_id: str
    params: dict


async def resolve_model(
    category: ModelCategory,
    *,
    model_id: str | None = None,
    capability: str | None = None,
) -> ResolvedModel:
    """解析要使用的模型與供應商設定。

    Args:
        category: 模型類別（text / image / video）。
        model_id: 任務指定的模型設定 ID；為空時使用預設。
        capability: 決定使用哪個 base URL，預設與 category 相同。

    Raises:
        ConfigurationError: 找不到可用模型，或供應商金鑰未設定。
    """

    from studio.integrations import build_config

    capability = capability or category.value

    async with session_scope() as session:
        model: Model | None = None

        if model_id:
            model = await session.get(Model, model_id)
            if model is None:
                raise ConfigurationError(f"指定的模型不存在：{model_id}")
        else:
            settings = await session.get(ModelSettings, 1)
            default_id = None
            if settings is not None:
                default_id = {
                    ModelCategory.text: settings.default_text_model_id,
                    ModelCategory.image: settings.default_image_model_id,
                    ModelCategory.video: settings.default_video_model_id,
                }.get(category)

            if default_id:
                model = await session.get(Model, default_id)

            if model is None:
                # 沒有明確預設時，若該類別只有一個啟用的模型就直接採用；
                # 有多個則要求使用者明確設定，避免不可預期的選擇。
                candidates = list(
                    (
                        await session.execute(
                            select(Model).where(Model.category == category, Model.enabled == 1).limit(2)
                        )
                    )
                    .scalars()
                    .all()
                )
                if len(candidates) == 1:
                    model = candidates[0]
                elif not candidates:
                    raise ConfigurationError(
                        f"尚未設定任何啟用的 {category.value} 模型，請先到設定頁新增供應商與模型"
                    )
                else:
                    raise ConfigurationError(
                        f"有多個 {category.value} 模型可用，請在設定頁指定預設模型"
                    )

        if not model.enabled:
            raise ConfigurationError(f"模型未啟用：{model.name}")

        provider: Provider | None = await session.get(Provider, model.provider_id)
        if provider is None:
            raise ConfigurationError(f"模型 {model.name} 的供應商不存在")

        config = build_config(provider, capability=capability)
        if not config.api_key:
            raise ConfigurationError(
                f"供應商 {provider.name} 的金鑰環境變數 {provider.api_key_env or '(未設定)'} 未提供值"
            )

        return ResolvedModel(
            model_name=model.model_id,
            provider_kind=provider.kind.value,
            config=config,
            model_id=model.id,
            provider_id=provider.id,
            params=dict(model.params or {}),
        )
