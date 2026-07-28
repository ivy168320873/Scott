/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 建立場景。
 */
export type SceneCreate = {
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
     * 場地類型（室內／室外）
     */
    location_type?: string;
    /**
     * 時間（日／夜／黃昏）
     */
    time_of_day?: string;
};

