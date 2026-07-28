/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PromptCategory } from './PromptCategory';
/**
 * 更新提示詞模板；未傳入的欄位保留原值。
 */
export type PromptTemplateUpdate = {
    category?: (PromptCategory | null);
    name?: (string | null);
    content?: (string | null);
    description?: (string | null);
    is_default?: (boolean | null);
    variables?: (Array<string> | null);
};

