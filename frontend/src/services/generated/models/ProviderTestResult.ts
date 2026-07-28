/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 供應商連線測試結果。
 */
export type ProviderTestResult = {
    /**
     * 供應商 ID
     */
    provider_id: string;
    /**
     * 設定是否就緒
     */
    ok: boolean;
    /**
     * 說明；失敗時指出缺少什麼，但不含金鑰內容
     */
    detail: string;
};

