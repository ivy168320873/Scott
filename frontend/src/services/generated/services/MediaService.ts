/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_list_MediaUsageRead__ } from '../models/ApiResponse_list_MediaUsageRead__';
import type { ApiResponse_MediaRead_ } from '../models/ApiResponse_MediaRead_';
import type { ApiResponse_MediaUsageRead_ } from '../models/ApiResponse_MediaUsageRead_';
import type { ApiResponse_OkData_ } from '../models/ApiResponse_OkData_';
import type { ApiResponse_Page_MediaRead__ } from '../models/ApiResponse_Page_MediaRead__';
import type { FileType } from '../models/FileType';
import type { MediaCreate } from '../models/MediaCreate';
import type { MediaUpdate } from '../models/MediaUpdate';
import type { MediaUsageCreate } from '../models/MediaUsageCreate';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class MediaService {
    /**
     * 列出媒體檔案
     * 分頁列出媒體檔案。
     * @returns ApiResponse_Page_MediaRead__ Successful Response
     * @throws ApiError
     */
    public static listMedia({
        search,
        projectId,
        fileType,
        page = 1,
        pageSize = 20,
    }: {
        /**
         * 以檔名做模糊搜尋
         */
        search?: (string | null),
        /**
         * 依專案過濾
         */
        projectId?: (string | null),
        /**
         * 依檔案類型過濾
         */
        fileType?: (FileType | null),
        /**
         * 頁碼，從 1 開始
         */
        page?: number,
        /**
         * 每頁筆數
         */
        pageSize?: number,
    }): CancelablePromise<ApiResponse_Page_MediaRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/media',
            query: {
                'search': search,
                'project_id': projectId,
                'file_type': fileType,
                'page': page,
                'page_size': pageSize,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 登記媒體檔案
     * 登記一筆媒體檔案中繼資料。
     * @returns ApiResponse_MediaRead_ Successful Response
     * @throws ApiError
     */
    public static createMedia({
        requestBody,
    }: {
        requestBody: MediaCreate,
    }): CancelablePromise<ApiResponse_MediaRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/media',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 取得媒體檔案
     * 取得單一媒體檔案的中繼資料。
     * @returns ApiResponse_MediaRead_ Successful Response
     * @throws ApiError
     */
    public static getMedia({
        fileId,
    }: {
        fileId: string,
    }): CancelablePromise<ApiResponse_MediaRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/media/{file_id}',
            path: {
                'file_id': fileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 更新媒體中繼資料
     * 更新媒體中繼資料；未傳入的欄位保留原值。
     * @returns ApiResponse_MediaRead_ Successful Response
     * @throws ApiError
     */
    public static updateMedia({
        fileId,
        requestBody,
    }: {
        fileId: string,
        requestBody: MediaUpdate,
    }): CancelablePromise<ApiResponse_MediaRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/studio/media/{file_id}',
            path: {
                'file_id': fileId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 刪除媒體檔案
     * 刪除媒體紀錄；預設保留實體檔案。
     * @returns ApiResponse_OkData_ Successful Response
     * @throws ApiError
     */
    public static deleteMedia({
        fileId,
        purgeObject = false,
    }: {
        fileId: string,
        /**
         * 是否一併刪除物件儲存中的實體檔案（不可回復）
         */
        purgeObject?: boolean,
    }): CancelablePromise<ApiResponse_OkData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/studio/media/{file_id}',
            path: {
                'file_id': fileId,
            },
            query: {
                'purge_object': purgeObject,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 列出媒體用途
     * 列出某檔案被哪些業務資源引用，避免誤刪仍在使用的素材。
     * @returns ApiResponse_list_MediaUsageRead__ Successful Response
     * @throws ApiError
     */
    public static listMediaUsages({
        fileId,
    }: {
        fileId: string,
    }): CancelablePromise<ApiResponse_list_MediaUsageRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/media/{file_id}/usages',
            path: {
                'file_id': fileId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 登記媒體用途
     * 登記檔案在業務鏈上的用途。
     * @returns ApiResponse_MediaUsageRead_ Successful Response
     * @throws ApiError
     */
    public static addMediaUsage({
        fileId,
        requestBody,
    }: {
        fileId: string,
        requestBody: MediaUsageCreate,
    }): CancelablePromise<ApiResponse_MediaUsageRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/media/{file_id}/usages',
            path: {
                'file_id': fileId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
