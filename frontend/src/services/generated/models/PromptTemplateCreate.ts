/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PromptCategory } from './PromptCategory';
/**
 * 建立提示詞模板。
 */
export type PromptTemplateCreate = {
    /**
     * 模板類別
     */
    category: PromptCategory;
    /**
     * 模板名稱
     */
    name: string;
    /**
     * 模板內容，含 {變數} 佔位
     */
    content?: string;
    /**
     * 說明
     */
    description?: string;
    /**
     * 是否為該類別的預設模板
     */
    is_default?: boolean;
    /**
     * 所屬專案；留空為全域模板
     */
    project_id?: (string | null);
    /**
     * 可用變數名稱
     */
    variables?: Array<string>;
};

