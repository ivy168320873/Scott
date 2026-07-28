/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 錯誤內容。
 */
export type ErrorBody = {
    /**
     * 機器可讀錯誤碼
     */
    code: string;
    /**
     * 人類可讀錯誤訊息
     */
    message: string;
    /**
     * 額外結構化資訊
     */
    details?: (Record<string, any> | null);
};

