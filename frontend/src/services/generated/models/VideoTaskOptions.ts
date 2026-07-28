/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 影片生成選項。
 */
export type VideoTaskOptions = {
    /**
     * 指定影片模型；留空使用預設
     */
    model_id?: (string | null);
    /**
     * 重試次數（影片成本高，預設不重試）
     */
    max_retries?: number;
};

