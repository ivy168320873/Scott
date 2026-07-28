/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ErrorBody } from './ErrorBody';
import type { ShotDialogueCandidateRead } from './ShotDialogueCandidateRead';
export type ApiResponse_ShotDialogueCandidateRead_ = {
    /**
     * 是否成功
     */
    success?: boolean;
    /**
     * 回應資料
     */
    data?: (ShotDialogueCandidateRead | null);
    /**
     * 錯誤內容；成功時為 null
     */
    error?: (ErrorBody | null);
};

