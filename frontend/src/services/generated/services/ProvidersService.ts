/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_OkData_ } from '../models/ApiResponse_OkData_';
import type { ApiResponse_Page_ProviderRead__ } from '../models/ApiResponse_Page_ProviderRead__';
import type { ApiResponse_ProviderRead_ } from '../models/ApiResponse_ProviderRead_';
import type { ApiResponse_ProviderTestResult_ } from '../models/ApiResponse_ProviderTestResult_';
import type { ProviderCreate } from '../models/ProviderCreate';
import type { ProviderKind } from '../models/ProviderKind';
import type { ProviderUpdate } from '../models/ProviderUpdate';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ProvidersService {
    /**
     * 列出供應商
     * 分頁列出供應商。回應不含任何金鑰內容。
     * @returns ApiResponse_Page_ProviderRead__ Successful Response
     * @throws ApiError
     */
    public static listProviders({
        search,
        providerType,
        page = 1,
        pageSize = 20,
    }: {
        /**
         * 以名稱、說明做模糊搜尋
         */
        search?: (string | null),
        /**
         * 依協定類型過濾
         */
        providerType?: (ProviderKind | null),
        /**
         * 頁碼，從 1 開始
         */
        page?: number,
        /**
         * 每頁筆數
         */
        pageSize?: number,
    }): CancelablePromise<ApiResponse_Page_ProviderRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/providers',
            query: {
                'search': search,
                'provider_type': providerType,
                'page': page,
                'page_size': pageSize,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 建立供應商
     * 建立供應商。`api_key_env` 只接受環境變數名稱，不接受金鑰本身。
     * @returns ApiResponse_ProviderRead_ Successful Response
     * @throws ApiError
     */
    public static createProvider({
        requestBody,
    }: {
        requestBody: ProviderCreate,
    }): CancelablePromise<ApiResponse_ProviderRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/providers',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 取得供應商
     * 取得單一供應商。回應不含任何金鑰內容。
     * @returns ApiResponse_ProviderRead_ Successful Response
     * @throws ApiError
     */
    public static getProvider({
        providerId,
    }: {
        providerId: string,
    }): CancelablePromise<ApiResponse_ProviderRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/providers/{provider_id}',
            path: {
                'provider_id': providerId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 更新供應商
     * 更新供應商；未傳入的欄位（含 `api_key_env`）保留原值。
     * @returns ApiResponse_ProviderRead_ Successful Response
     * @throws ApiError
     */
    public static updateProvider({
        providerId,
        requestBody,
    }: {
        providerId: string,
        requestBody: ProviderUpdate,
    }): CancelablePromise<ApiResponse_ProviderRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/studio/providers/{provider_id}',
            path: {
                'provider_id': providerId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 刪除供應商
     * 刪除供應商及其模型設定。
     * @returns ApiResponse_OkData_ Successful Response
     * @throws ApiError
     */
    public static deleteProvider({
        providerId,
    }: {
        providerId: string,
    }): CancelablePromise<ApiResponse_OkData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/studio/providers/{provider_id}',
            path: {
                'provider_id': providerId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 測試供應商設定
     * 檢查供應商設定是否就緒。回應只說明缺少什麼，不含金鑰內容。
     * @returns ApiResponse_ProviderTestResult_ Successful Response
     * @throws ApiError
     */
    public static testProvider({
        providerId,
    }: {
        providerId: string,
    }): CancelablePromise<ApiResponse_ProviderTestResult_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/providers/{provider_id}/test',
            path: {
                'provider_id': providerId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
