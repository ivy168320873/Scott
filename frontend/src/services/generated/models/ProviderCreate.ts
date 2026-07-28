/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProviderKind } from './ProviderKind';
/**
 * 建立供應商。
 */
export type ProviderCreate = {
    /**
     * 供應商顯示名稱
     */
    name: string;
    /**
     * 協定類型，決定使用哪個 Adapter
     */
    provider_type: ProviderKind;
    /**
     * API 金鑰所在的**環境變數名稱**；不要填入金鑰本身
     */
    api_key_env?: string;
    /**
     * 文字 API base URL
     */
    base_url?: string;
    /**
     * 圖片 API base URL
     */
    image_base_url?: string;
    /**
     * 影片 API base URL
     */
    video_base_url?: string;
    /**
     * 是否啟用
     */
    enabled?: boolean;
    /**
     * 說明
     */
    description?: string;
    /**
     * 請求逾時（秒）
     */
    timeout_seconds?: number;
    /**
     * 失敗重試次數
     */
    max_retries?: number;
    /**
     * 供應商專屬設定
     */
    extra_config?: Record<string, any>;
};

