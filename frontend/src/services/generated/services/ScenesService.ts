/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_AssetImageRead_ } from '../models/ApiResponse_AssetImageRead_';
import type { ApiResponse_dict_ } from '../models/ApiResponse_dict_';
import type { ApiResponse_list_AssetImageRead__ } from '../models/ApiResponse_list_AssetImageRead__';
import type { ApiResponse_OkData_ } from '../models/ApiResponse_OkData_';
import type { ApiResponse_Page_SceneRead__ } from '../models/ApiResponse_Page_SceneRead__';
import type { ApiResponse_SceneRead_ } from '../models/ApiResponse_SceneRead_';
import type { AssetImageCreate } from '../models/AssetImageCreate';
import type { SceneCreate } from '../models/SceneCreate';
import type { SceneUpdate } from '../models/SceneUpdate';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ScenesService {
    /**
     * 列出scenes
     * 分頁列出資產。
     * @returns ApiResponse_Page_SceneRead__ Successful Response
     * @throws ApiError
     */
    public static listScenes({
        projectId,
        search,
        orderBy,
        page = 1,
        pageSize = 20,
    }: {
        projectId: (string | null),
        /**
         * 以名稱、描述做模糊搜尋
         */
        search?: (string | null),
        /**
         * 排序欄位；前綴 - 表示遞減
         */
        orderBy?: (string | null),
        /**
         * 頁碼，從 1 開始
         */
        page?: number,
        /**
         * 每頁筆數
         */
        pageSize?: number,
    }): CancelablePromise<ApiResponse_Page_SceneRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/projects/{project_id}/scenes',
            path: {
                'project_id': projectId,
            },
            query: {
                'search': search,
                'order_by': orderBy,
                'page': page,
                'page_size': pageSize,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 建立Scene
     * 建立資產。
     * @returns ApiResponse_SceneRead_ Successful Response
     * @throws ApiError
     */
    public static createScene({
        projectId,
        requestBody,
    }: {
        projectId: (string | null),
        requestBody: SceneCreate,
    }): CancelablePromise<ApiResponse_SceneRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/projects/{project_id}/scenes',
            path: {
                'project_id': projectId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 取得Scene
     * 取得單一資產。
     * @returns ApiResponse_SceneRead_ Successful Response
     * @throws ApiError
     */
    public static getScene({
        assetId,
    }: {
        assetId: string,
    }): CancelablePromise<ApiResponse_SceneRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/scenes/{asset_id}',
            path: {
                'asset_id': assetId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 更新Scene
     * 更新資產；未傳入的欄位保留原值。
     * @returns ApiResponse_SceneRead_ Successful Response
     * @throws ApiError
     */
    public static updateScene({
        assetId,
        requestBody,
    }: {
        assetId: string,
        requestBody: SceneUpdate,
    }): CancelablePromise<ApiResponse_SceneRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/studio/scenes/{asset_id}',
            path: {
                'asset_id': assetId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 刪除Scene
     * 刪除資產。
     * @returns ApiResponse_OkData_ Successful Response
     * @throws ApiError
     */
    public static deleteScene({
        assetId,
    }: {
        assetId: string,
    }): CancelablePromise<ApiResponse_OkData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/studio/scenes/{asset_id}',
            path: {
                'asset_id': assetId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 檢查Scene名稱是否已存在
     * 檢查名稱是否已存在，引導使用者沿用既有資產而非建立分身。
     * @returns ApiResponse_dict_ Successful Response
     * @throws ApiError
     */
    public static checkSceneExists({
        name,
        projectId,
    }: {
        /**
         * 要檢查的名稱
         */
        name: string,
        projectId?: (string | null),
    }): CancelablePromise<ApiResponse_dict_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/scenes/exists/check',
            query: {
                'name': name,
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 列出Scene參考圖
     * 列出資產的參考圖。
     * @returns ApiResponse_list_AssetImageRead__ Successful Response
     * @throws ApiError
     */
    public static listSceneImages({
        assetId,
    }: {
        assetId: string,
    }): CancelablePromise<ApiResponse_list_AssetImageRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/scenes/{asset_id}/images',
            path: {
                'asset_id': assetId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 新增Scene參考圖
     * 為資產新增參考圖；設為主要時會取消其他圖片的主要標記。
     * @returns ApiResponse_AssetImageRead_ Successful Response
     * @throws ApiError
     */
    public static addSceneImage({
        assetId,
        requestBody,
    }: {
        assetId: string,
        requestBody: AssetImageCreate,
    }): CancelablePromise<ApiResponse_AssetImageRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/scenes/{asset_id}/images',
            path: {
                'asset_id': assetId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 刪除Scene參考圖
     * 刪除資產參考圖。
     * @returns ApiResponse_OkData_ Successful Response
     * @throws ApiError
     */
    public static deleteSceneImage({
        imageId,
    }: {
        imageId: string,
    }): CancelablePromise<ApiResponse_OkData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/studio/scenes/images/{image_id}',
            path: {
                'image_id': imageId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
