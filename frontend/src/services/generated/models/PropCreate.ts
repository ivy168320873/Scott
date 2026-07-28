/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 建立道具。
 */
export type PropCreate = {
    /**
     * 資產名稱
     */
    name: string;
    /**
     * 描述
     */
    description?: string;
    /**
     * 外觀提示詞；生成時附加於鏡頭提示詞以維持一致性
     */
    appearance_prompt?: string;
    /**
     * 結構化屬性
     */
    attributes?: Record<string, any>;
    /**
     * 道具分類
     */
    category?: string;
};

