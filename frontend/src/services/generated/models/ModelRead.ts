/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ModelCategory } from './ModelCategory';
/**
 * 模型設定對外表示。
 */
export type ModelRead = {
    /**
     * 模型設定 ID
     */
    id: string;
    /**
     * 所屬供應商 ID
     */
    provider_id: string;
    /**
     * 供應商名稱（便於前端顯示）
     */
    provider_name?: string;
    /**
     * 顯示名稱
     */
    name: string;
    /**
     * 供應商端模型識別碼
     */
    model_id: string;
    /**
     * 模型類別
     */
    category: ModelCategory;
    /**
     * 是否啟用
     */
    enabled: boolean;
    /**
     * 預設呼叫參數
     */
    params: Record<string, any>;
    /**
     * 說明
     */
    description: string;
    /**
     * 建立時間
     */
    created_at: string;
    /**
     * 更新時間
     */
    updated_at: string;
};

