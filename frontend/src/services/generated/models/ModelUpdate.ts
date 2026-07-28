/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ModelCategory } from './ModelCategory';
/**
 * 更新模型設定；未傳入的欄位保留原值。
 */
export type ModelUpdate = {
    name?: (string | null);
    model_id?: (string | null);
    category?: (ModelCategory | null);
    enabled?: (boolean | null);
    params?: (Record<string, any> | null);
    description?: (string | null);
};

