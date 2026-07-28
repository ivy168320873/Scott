/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProviderKind } from './ProviderKind';
/**
 * 供應商對外表示。
 *
 * 刻意 **不含** `api_key`、`api_secret`、`token` 等任何金鑰欄位。
 * `api_key_env` 只是環境變數名稱，不是機密；
 * `api_key_configured` 只表示該變數是否已設定。
 */
export type ProviderRead = {
    /**
     * 供應商 ID
     */
    id: string;
    /**
     * 供應商名稱
     */
    name: string;
    /**
     * 協定類型
     */
    provider_type: ProviderKind;
    /**
     * 文字 API base URL
     */
    base_url: string;
    /**
     * 圖片 API base URL
     */
    image_base_url: string;
    /**
     * 影片 API base URL
     */
    video_base_url: string;
    /**
     * 是否啟用
     */
    enabled: boolean;
    /**
     * 金鑰所在的環境變數名稱（非機密）
     */
    api_key_env: string;
    /**
     * 該環境變數目前是否已設定；不透露金鑰內容
     */
    api_key_configured: boolean;
    /**
     * 說明
     */
    description: string;
    /**
     * 請求逾時（秒）
     */
    timeout_seconds: number;
    /**
     * 失敗重試次數
     */
    max_retries: number;
    /**
     * 建立時間
     */
    created_at: string;
    /**
     * 更新時間
     */
    updated_at: string;
};

