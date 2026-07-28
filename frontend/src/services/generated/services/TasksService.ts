/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_list_TaskLinkRead__ } from '../models/ApiResponse_list_TaskLinkRead__';
import type { ApiResponse_list_TaskRead__ } from '../models/ApiResponse_list_TaskRead__';
import type { ApiResponse_OkData_ } from '../models/ApiResponse_OkData_';
import type { ApiResponse_Page_TaskRead__ } from '../models/ApiResponse_Page_TaskRead__';
import type { ApiResponse_TaskLinkRead_ } from '../models/ApiResponse_TaskLinkRead_';
import type { ApiResponse_TaskRead_ } from '../models/ApiResponse_TaskRead_';
import type { TaskCancel } from '../models/TaskCancel';
import type { TaskCreate } from '../models/TaskCreate';
import type { TaskLinkCreate } from '../models/TaskLinkCreate';
import type { TaskLinkUpdate } from '../models/TaskLinkUpdate';
import type { TaskStatus } from '../models/TaskStatus';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class TasksService {
    /**
     * 列出生成任務
     * 分頁列出任務（依最近更新排序）。
     * @returns ApiResponse_Page_TaskRead__ Successful Response
     * @throws ApiError
     */
    public static listTasks({
        search,
        status,
        taskKind,
        projectId,
        chapterId,
        shotId,
        page = 1,
        pageSize = 20,
    }: {
        /**
         * 以任務類型、步驟描述做模糊搜尋
         */
        search?: (string | null),
        /**
         * 依狀態過濾
         */
        status?: (TaskStatus | null),
        /**
         * 依任務類型過濾
         */
        taskKind?: (string | null),
        /**
         * 依專案過濾
         */
        projectId?: (string | null),
        /**
         * 依章節過濾
         */
        chapterId?: (string | null),
        /**
         * 依分鏡過濾
         */
        shotId?: (string | null),
        /**
         * 頁碼，從 1 開始
         */
        page?: number,
        /**
         * 每頁筆數
         */
        pageSize?: number,
    }): CancelablePromise<ApiResponse_Page_TaskRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/tasks',
            query: {
                'search': search,
                'status': status,
                'task_kind': taskKind,
                'project_id': projectId,
                'chapter_id': chapterId,
                'shot_id': shotId,
                'page': page,
                'page_size': pageSize,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 建立生成任務
     * 建立任務紀錄。關聯資源不存在時回 404。
     * @returns ApiResponse_TaskRead_ Successful Response
     * @throws ApiError
     */
    public static createTask({
        requestBody,
    }: {
        requestBody: TaskCreate,
    }): CancelablePromise<ApiResponse_TaskRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/tasks',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 列出進行中的任務
     * 列出所有尚未結束的任務。
     *
     * 任務狀態存於資料庫，因此重新整理頁面或重啟服務後仍可恢復顯示。
     * @returns ApiResponse_list_TaskRead__ Successful Response
     * @throws ApiError
     */
    public static listActiveTasks(): CancelablePromise<ApiResponse_list_TaskRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/tasks/active',
        });
    }
    /**
     * 取得任務
     * 取得單一任務的完整狀態。
     * @returns ApiResponse_TaskRead_ Successful Response
     * @throws ApiError
     */
    public static getTask({
        taskId,
    }: {
        taskId: string,
    }): CancelablePromise<ApiResponse_TaskRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/tasks/{task_id}',
            path: {
                'task_id': taskId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 刪除任務紀錄
     * 刪除任務紀錄。進行中的任務回 409，請先取消。
     * @returns ApiResponse_OkData_ Successful Response
     * @throws ApiError
     */
    public static deleteTask({
        taskId,
    }: {
        taskId: string,
    }): CancelablePromise<ApiResponse_OkData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/studio/tasks/{task_id}',
            path: {
                'task_id': taskId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 請求取消任務
     * 請求取消任務。已結束的任務回 409。
     * @returns ApiResponse_TaskRead_ Successful Response
     * @throws ApiError
     */
    public static cancelTask({
        taskId,
        requestBody,
    }: {
        taskId: string,
        requestBody: TaskCancel,
    }): CancelablePromise<ApiResponse_TaskRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/tasks/{task_id}/cancel',
            path: {
                'task_id': taskId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 列出任務產物
     * 列出任務的產物關聯與各自的採用狀態。
     * @returns ApiResponse_list_TaskLinkRead__ Successful Response
     * @throws ApiError
     */
    public static listTaskLinks({
        taskId,
    }: {
        taskId: string,
    }): CancelablePromise<ApiResponse_list_TaskLinkRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/tasks/{task_id}/links',
            path: {
                'task_id': taskId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 建立任務產物關聯
     * 建立任務與業務資源／產物檔案的關聯。
     * @returns ApiResponse_TaskLinkRead_ Successful Response
     * @throws ApiError
     */
    public static createTaskLink({
        taskId,
        requestBody,
    }: {
        taskId: string,
        requestBody: TaskLinkCreate,
    }): CancelablePromise<ApiResponse_TaskLinkRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/tasks/{task_id}/links',
            path: {
                'task_id': taskId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 更新產物採用狀態
     * 更新任務產物的採用狀態（accepted / todo / rejected）。
     * @returns ApiResponse_TaskLinkRead_ Successful Response
     * @throws ApiError
     */
    public static updateTaskLink({
        linkId,
        requestBody,
    }: {
        linkId: number,
        requestBody: TaskLinkUpdate,
    }): CancelablePromise<ApiResponse_TaskLinkRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/studio/task-links/{link_id}',
            path: {
                'link_id': linkId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
