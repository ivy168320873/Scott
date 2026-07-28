/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 場景對外表示。
 */
export type SceneRead = {
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
     * 所屬專案 ID
     */
    project_id: string;
    /**
     * 場地類型
     */
    location_type: string;
    /**
     * 時間
     */
    time_of_day: string;
};

