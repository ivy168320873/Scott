/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 圖片生成選項。
 */
export type ImageTaskOptions = {
    /**
     * 指定圖片模型；留空使用預設
     */
    model_id?: (string | null);
    /**
     * 要生成的幀類型
     */
    frame_types?: Array<string>;
    /**
     * 重試次數
     */
    max_retries?: number;
};

