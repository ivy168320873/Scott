/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_ChapterRead_ } from '../models/ApiResponse_ChapterRead_';
import type { ApiResponse_OkData_ } from '../models/ApiResponse_OkData_';
import type { ApiResponse_Page_ChapterRead__ } from '../models/ApiResponse_Page_ChapterRead__';
import type { ApiResponse_Page_ProjectRead__ } from '../models/ApiResponse_Page_ProjectRead__';
import type { ApiResponse_ProjectRead_ } from '../models/ApiResponse_ProjectRead_';
import type { ApiResponse_ScriptRead_ } from '../models/ApiResponse_ScriptRead_';
import type { ChapterCreate } from '../models/ChapterCreate';
import type { ChapterStatus } from '../models/ChapterStatus';
import type { ChapterUpdate } from '../models/ChapterUpdate';
import type { ProjectCreate } from '../models/ProjectCreate';
import type { ProjectStatus } from '../models/ProjectStatus';
import type { ProjectUpdate } from '../models/ProjectUpdate';
import type { ScriptUpdate } from '../models/ScriptUpdate';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ProjectsService {
    /**
     * 列出專案
     * 分頁列出專案。
     * @returns ApiResponse_Page_ProjectRead__ Successful Response
     * @throws ApiError
     */
    public static listProjects({
        search,
        status,
        orderBy,
        page = 1,
        pageSize = 20,
    }: {
        /**
         * 以名稱、簡介、題材做模糊搜尋
         */
        search?: (string | null),
        /**
         * 依狀態過濾
         */
        status?: (ProjectStatus | null),
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
    }): CancelablePromise<ApiResponse_Page_ProjectRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/projects',
            query: {
                'search': search,
                'status': status,
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
     * 建立專案
     * 建立專案。
     * @returns ApiResponse_ProjectRead_ Successful Response
     * @throws ApiError
     */
    public static createProject({
        requestBody,
    }: {
        requestBody: ProjectCreate,
    }): CancelablePromise<ApiResponse_ProjectRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/projects',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 取得專案
     * 取得單一專案。
     * @returns ApiResponse_ProjectRead_ Successful Response
     * @throws ApiError
     */
    public static getProject({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<ApiResponse_ProjectRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/projects/{project_id}',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 更新專案
     * 更新專案；未傳入的欄位保留原值。
     * @returns ApiResponse_ProjectRead_ Successful Response
     * @throws ApiError
     */
    public static updateProject({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: ProjectUpdate,
    }): CancelablePromise<ApiResponse_ProjectRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/studio/projects/{project_id}',
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
     * 刪除專案
     * 刪除專案及其所有下層資料。
     * @returns ApiResponse_OkData_ Successful Response
     * @throws ApiError
     */
    public static deleteProject({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<ApiResponse_OkData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/studio/projects/{project_id}',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 封存專案
     * 封存專案（保留資料的安全替代方案）。
     * @returns ApiResponse_ProjectRead_ Successful Response
     * @throws ApiError
     */
    public static archiveProject({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<ApiResponse_ProjectRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/projects/{project_id}/archive',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 重算專案統計
     * 重算專案的聚合統計。
     * @returns ApiResponse_ProjectRead_ Successful Response
     * @throws ApiError
     */
    public static refreshProjectStats({
        projectId,
    }: {
        projectId: string,
    }): CancelablePromise<ApiResponse_ProjectRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/projects/{project_id}/refresh-stats',
            path: {
                'project_id': projectId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 列出章節
     * 分頁列出某專案的章節（依序號遞增）。
     * @returns ApiResponse_Page_ChapterRead__ Successful Response
     * @throws ApiError
     */
    public static listChapters({
        projectId,
        search,
        status,
        page = 1,
        pageSize = 20,
    }: {
        projectId: string,
        /**
         * 以標題、摘要做模糊搜尋
         */
        search?: (string | null),
        /**
         * 依狀態過濾
         */
        status?: (ChapterStatus | null),
        /**
         * 頁碼，從 1 開始
         */
        page?: number,
        /**
         * 每頁筆數
         */
        pageSize?: number,
    }): CancelablePromise<ApiResponse_Page_ChapterRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/projects/{project_id}/chapters',
            path: {
                'project_id': projectId,
            },
            query: {
                'search': search,
                'status': status,
                'page': page,
                'page_size': pageSize,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 建立章節
     * 建立章節；序號留空時自動接續。
     * @returns ApiResponse_ChapterRead_ Successful Response
     * @throws ApiError
     */
    public static createChapter({
        projectId,
        requestBody,
    }: {
        projectId: string,
        requestBody: ChapterCreate,
    }): CancelablePromise<ApiResponse_ChapterRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/projects/{project_id}/chapters',
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
     * 取得章節
     * 取得單一章節。
     * @returns ApiResponse_ChapterRead_ Successful Response
     * @throws ApiError
     */
    public static getChapter({
        chapterId,
    }: {
        chapterId: string,
    }): CancelablePromise<ApiResponse_ChapterRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/chapters/{chapter_id}',
            path: {
                'chapter_id': chapterId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 更新章節
     * 更新章節；未傳入的欄位保留原值。
     * @returns ApiResponse_ChapterRead_ Successful Response
     * @throws ApiError
     */
    public static updateChapter({
        chapterId,
        requestBody,
    }: {
        chapterId: string,
        requestBody: ChapterUpdate,
    }): CancelablePromise<ApiResponse_ChapterRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/studio/chapters/{chapter_id}',
            path: {
                'chapter_id': chapterId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 刪除章節
     * 刪除章節及其分鏡。
     * @returns ApiResponse_OkData_ Successful Response
     * @throws ApiError
     */
    public static deleteChapter({
        chapterId,
    }: {
        chapterId: string,
    }): CancelablePromise<ApiResponse_OkData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/studio/chapters/{chapter_id}',
            path: {
                'chapter_id': chapterId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 取得章節腳本
     * 取得章節腳本原文與精簡稿。
     * @returns ApiResponse_ScriptRead_ Successful Response
     * @throws ApiError
     */
    public static getScript({
        chapterId,
    }: {
        chapterId: string,
    }): CancelablePromise<ApiResponse_ScriptRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/chapters/{chapter_id}/script',
            path: {
                'chapter_id': chapterId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 更新章節腳本
     * 更新腳本原文；內容變更時會清空既有精簡稿。
     * @returns ApiResponse_ScriptRead_ Successful Response
     * @throws ApiError
     */
    public static updateScript({
        chapterId,
        requestBody,
    }: {
        chapterId: string,
        requestBody: ScriptUpdate,
    }): CancelablePromise<ApiResponse_ScriptRead_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/v1/studio/chapters/{chapter_id}/script',
            path: {
                'chapter_id': chapterId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
