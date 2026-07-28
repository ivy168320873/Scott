/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ModelCategory } from './ModelCategory';
/**
 * 建立模型設定。
 */
export type ModelCreate = {
    /**
     * 所屬供應商 ID
     */
    provider_id: string;
    /**
     * 顯示名稱
     */
    name: string;
    /**
     * 供應商端的實際模型識別碼
     */
    model_id: string;
    /**
     * 模型類別
     */
    category: ModelCategory;
    /**
     * 是否啟用
     */
    enabled?: boolean;
    /**
     * 預設呼叫參數
     */
    params?: Record<string, any>;
    /**
     * 說明
     */
    description?: string;
};

