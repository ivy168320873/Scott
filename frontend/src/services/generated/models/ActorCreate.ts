/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 建立演員。
 */
export type ActorCreate = {
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
     * 性別
     */
    gender?: string;
    /**
     * 年齡區間
     */
    age_range?: string;
    /**
     * 主要參考圖檔案 ID
     */
    reference_file_id?: (string | null);
};

