/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_ModelRead_ } from '../models/ApiResponse_ModelRead_';
import type { ApiResponse_OkData_ } from '../models/ApiResponse_OkData_';
import type { ApiResponse_Page_ModelRead__ } from '../models/ApiResponse_Page_ModelRead__';
import type { ModelCategory } from '../models/ModelCategory';
import type { ModelCreate } from '../models/ModelCreate';
import type { ModelUpdate } from '../models/ModelUpdate';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ModelsService {
    /**
     * 列出模型
     * 分頁列出模型設定。
     * @returns ApiResponse_Page_ModelRead__ Successful Response
     * @throws ApiError
     */
    public static listModels({
        search,
        providerId,
        category,
        page = 1,
        pageSize = 20,
    }: {
        /**
         * 以名稱、模型識別碼做模糊搜尋
         */
        search?: (string | null),
        /**
         * 依供應商過濾
         */
        providerId?: (string | null),
        /**
         * 依類別過濾
         */
        category?: (ModelCategory | null),
        /**
         * 頁碼，從 1 開始
         */
        page?: number,
        /**
         * 每頁筆數
         */
        pageSize?: number,
    }): CancelablePromise<ApiResponse_Page_ModelRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/models',
            query: {
                'search': search,
                'provider_id': providerId,
                'category': category,
                'page': page,
                'page_size': pageSize,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 建立模型
     * 建立模型設定。供應商不存在時回 404。
     * @returns ApiResponse_ModelRead_ Successful Response
     * @throws ApiError
     */
    public static createModel({
        requestBody,
    }: {
        requestBody: ModelCreate,
    }): CancelablePromise<ApiResponse_ModelRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/models',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 取得模型
     * 取得單一模型設定。
     * @returns ApiResponse_ModelRead_ Successful Response
     * @throws ApiError
     */
    public static getModel({
        modelId,
    }: {
        modelId: string,
    }): CancelablePromise<ApiResponse_ModelRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/models/{model_id}',
            path: {
                'model_id': modelId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 更新模型
     * 更新模型設定；未傳入的欄位保留原值。
     * @returns ApiResponse_ModelRead_ Successful Response
     * @throws ApiError
     */
    public static updateModel({
        modelId,
        requestBody,
    }: {
        modelId: string,
        requestBody: ModelUpdate,
    }): CancelablePromise<ApiResponse_ModelRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/studio/models/{model_id}',
            path: {
                'model_id': modelId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 刪除模型
     * 刪除模型設定。
     * @returns ApiResponse_OkData_ Successful Response
     * @throws ApiError
     */
    public static deleteModel({
        modelId,
    }: {
        modelId: string,
    }): CancelablePromise<ApiResponse_OkData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/studio/models/{model_id}',
            path: {
                'model_id': modelId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
