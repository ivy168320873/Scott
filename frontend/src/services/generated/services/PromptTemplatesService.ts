/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_OkData_ } from '../models/ApiResponse_OkData_';
import type { ApiResponse_Page_PromptTemplateRead__ } from '../models/ApiResponse_Page_PromptTemplateRead__';
import type { ApiResponse_PromptTemplateRead_ } from '../models/ApiResponse_PromptTemplateRead_';
import type { PromptCategory } from '../models/PromptCategory';
import type { PromptTemplateCreate } from '../models/PromptTemplateCreate';
import type { PromptTemplateUpdate } from '../models/PromptTemplateUpdate';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class PromptTemplatesService {
    /**
     * 列出提示詞模板
     * 分頁列出提示詞模板。
     * @returns ApiResponse_Page_PromptTemplateRead__ Successful Response
     * @throws ApiError
     */
    public static listPromptTemplates({
        search,
        category,
        projectId,
        page = 1,
        pageSize = 20,
    }: {
        /**
         * 以名稱、內容做模糊搜尋
         */
        search?: (string | null),
        /**
         * 依類別過濾
         */
        category?: (PromptCategory | null),
        /**
         * 依專案過濾；留空為全域模板
         */
        projectId?: (string | null),
        /**
         * 頁碼，從 1 開始
         */
        page?: number,
        /**
         * 每頁筆數
         */
        pageSize?: number,
    }): CancelablePromise<ApiResponse_Page_PromptTemplateRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/prompt-templates',
            query: {
                'search': search,
                'category': category,
                'project_id': projectId,
                'page': page,
                'page_size': pageSize,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 建立提示詞模板
     * 建立提示詞模板；設為預設時會取消同類別其他模板的預設標記。
     * @returns ApiResponse_PromptTemplateRead_ Successful Response
     * @throws ApiError
     */
    public static createPromptTemplate({
        requestBody,
    }: {
        requestBody: PromptTemplateCreate,
    }): CancelablePromise<ApiResponse_PromptTemplateRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/prompt-templates',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 取得提示詞模板
     * 取得單一提示詞模板。
     * @returns ApiResponse_PromptTemplateRead_ Successful Response
     * @throws ApiError
     */
    public static getPromptTemplate({
        templateId,
    }: {
        templateId: string,
    }): CancelablePromise<ApiResponse_PromptTemplateRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/prompt-templates/{template_id}',
            path: {
                'template_id': templateId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 更新提示詞模板
     * 更新提示詞模板；未傳入的欄位保留原值。
     * @returns ApiResponse_PromptTemplateRead_ Successful Response
     * @throws ApiError
     */
    public static updatePromptTemplate({
        templateId,
        requestBody,
    }: {
        templateId: string,
        requestBody: PromptTemplateUpdate,
    }): CancelablePromise<ApiResponse_PromptTemplateRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/studio/prompt-templates/{template_id}',
            path: {
                'template_id': templateId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 刪除提示詞模板
     * 刪除提示詞模板。
     * @returns ApiResponse_OkData_ Successful Response
     * @throws ApiError
     */
    public static deletePromptTemplate({
        templateId,
    }: {
        templateId: string,
    }): CancelablePromise<ApiResponse_OkData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/studio/prompt-templates/{template_id}',
            path: {
                'template_id': templateId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
