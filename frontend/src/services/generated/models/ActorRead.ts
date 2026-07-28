/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 演員對外表示。
 */
export type ActorRead = {
    /**
     * 資產 ID
     */
    id: string;
    /**
     * 資產名稱
     */
    name: string;
    /**
     * 描述
     */
    description: string;
    /**
     * 外觀提示詞
     */
    appearance_prompt: string;
    /**
     * 結構化屬性
     */
    attributes: Record<string, any>;
    /**
     * 建立時間
     */
    created_at: string;
    /**
     * 更新時間
     */
    updated_at: string;
    /**
     * 性別
     */
    gender: string;
    /**
     * 年齡區間
     */
    age_range: string;
    /**
     * 主要參考圖檔案 ID
     */
    reference_file_id: (string | null);
};

