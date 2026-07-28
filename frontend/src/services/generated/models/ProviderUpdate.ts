/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProviderKind } from './ProviderKind';
/**
 * 更新供應商。
 *
 * 所有欄位皆為選填：**未傳入的欄位保留原值**。
 * 特別是 `api_key_env` —— 只有明確傳入新值才會更新，
 * 因此前端送出表單時不需要（也不應該）回填既有設定。
 */
export type ProviderUpdate = {
    name?: (string | null);
    provider_type?: (ProviderKind | null);
    /**
     * 留空或不傳表示保留原設定
     */
    api_key_env?: (string | null);
    base_url?: (string | null);
    image_base_url?: (string | null);
    video_base_url?: (string | null);
    enabled?: (boolean | null);
    description?: (string | null);
    timeout_seconds?: (number | null);
    max_retries?: (number | null);
    extra_config?: (Record<string, any> | null);
};

