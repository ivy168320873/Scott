/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ErrorBody } from './ErrorBody';
import type { Page_CharacterRead_ } from './Page_CharacterRead_';
export type ApiResponse_Page_CharacterRead__ = {
    /**
     * 是否成功
     */
    success?: boolean;
    /**
     * 回應資料
     */
    data?: (Page_CharacterRead_ | null);
    /**
     * 錯誤內容；成功時為 null
     */
    error?: (ErrorBody | null);
};

