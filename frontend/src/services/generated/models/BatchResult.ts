/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 批次提交結果。
 */
export type BatchResult = {
    /**
     * 已建立的任務 ID
     */
    task_ids: Array<string>;
    /**
     * 被略過的分鏡與原因
     */
    skipped?: Array<Record<string, string>>;
};

