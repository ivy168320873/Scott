/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PromptCategory } from './PromptCategory';
/**
 * 提示詞模板對外表示。
 */
export type PromptTemplateRead = {
    /**
     * 模板 ID
     */
    id: string;
    /**
     * 模板類別
     */
    category: PromptCategory;
    /**
     * 模板名稱
     */
    name: string;
    /**
     * 模板內容
     */
    content: string;
    /**
     * 說明
     */
    description: string;
    /**
     * 是否為預設模板
     */
    is_default: boolean;
    /**
     * 所屬專案 ID；null 為全域
     */
    project_id: (string | null);
    /**
     * 可用變數名稱
     */
    variables: Array<string>;
    /**
     * 建立時間
     */
    created_at: string;
    /**
     * 更新時間
     */
    updated_at: string;
};

