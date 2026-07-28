/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 建立服裝。
 */
export type CostumeCreate = {
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
     * 預設穿著此服裝的角色 ID
     */
    character_id?: (string | null);
    /**
     * season／場合
     */
    season?: string;
};

