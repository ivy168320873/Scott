/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ErrorBody } from './ErrorBody';
import type { MediaUsageRead } from './MediaUsageRead';
export type ApiResponse_list_MediaUsageRead__ = {
    /**
     * 是否成功
     */
    success?: boolean;
    /**
     * 回應資料
     */
    data?: (Array<MediaUsageRead> | null);
    /**
     * 錯誤內容；成功時為 null
     */
    error?: (ErrorBody | null);
};

