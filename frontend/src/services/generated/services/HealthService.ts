/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_HealthData_ } from '../models/ApiResponse_HealthData_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class HealthService {
    /**
     * 健康檢查
     * 回報 Studio 與其相依元件的健康狀態。
     *
     * 整體狀態判定：
     * - `ok`：資料庫可連線，且所有「必要」元件正常。
     * - `degraded`：資料庫正常但有非必要元件異常（例如 inline 模式下 Redis 不可用）。
     *
     * Redis 在 `inline` 任務模式下屬非必要元件，不會使整體狀態變成失敗。
     * @returns ApiResponse_HealthData_ Successful Response
     * @throws ApiError
     */
    public static healthApiV1StudioHealthGet(): CancelablePromise<ApiResponse_HealthData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/health',
        });
    }
    /**
     * 存活探針
     * 僅確認 process 存活，不檢查任何外部相依。
     *
     * 供容器 liveness probe 使用：外部服務短暫異常不應導致容器被重啟。
     * @returns ApiResponse_HealthData_ Successful Response
     * @throws ApiError
     */
    public static livenessApiV1StudioHealthLiveGet(): CancelablePromise<ApiResponse_HealthData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/health/live',
        });
    }
}
