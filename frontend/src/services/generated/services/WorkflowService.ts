/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_BatchResult_ } from '../models/ApiResponse_BatchResult_';
import type { ApiResponse_TaskRead_ } from '../models/ApiResponse_TaskRead_';
import type { AssetImageTaskOptions } from '../models/AssetImageTaskOptions';
import type { BatchShotRequest } from '../models/BatchShotRequest';
import type { ImageTaskOptions } from '../models/ImageTaskOptions';
import type { ScriptTaskOptions } from '../models/ScriptTaskOptions';
import type { VideoTaskOptions } from '../models/VideoTaskOptions';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class WorkflowService {
    /**
     * 提交腳本拆分鏡任務
     * 把章節腳本拆解成分鏡。回傳任務物件，實際工作在背景執行。
     * @returns ApiResponse_TaskRead_ Successful Response
     * @throws ApiError
     */
    public static analyzeChapterScript({
        chapterId,
        requestBody,
    }: {
        chapterId: string,
        requestBody: ScriptTaskOptions,
    }): CancelablePromise<ApiResponse_TaskRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/chapters/{chapter_id}/analyze',
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
     * 提交實體提取任務
     * 從章節的所有分鏡提取角色／場景／道具／服裝／對白候選。
     * @returns ApiResponse_TaskRead_ Successful Response
     * @throws ApiError
     */
    public static extractChapterEntities({
        chapterId,
        requestBody,
    }: {
        chapterId: string,
        requestBody: ScriptTaskOptions,
    }): CancelablePromise<ApiResponse_TaskRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/chapters/{chapter_id}/extract',
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
     * 提交腳本精簡任務
     * 精簡腳本並寫入 condensed_text，供後續分析節省 token。
     * @returns ApiResponse_TaskRead_ Successful Response
     * @throws ApiError
     */
    public static simplifyChapterScript({
        chapterId,
        requestBody,
    }: {
        chapterId: string,
        requestBody: ScriptTaskOptions,
    }): CancelablePromise<ApiResponse_TaskRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/chapters/{chapter_id}/simplify',
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
     * 提交腳本優化任務
     * 產生優化建議稿；結果放在任務 result 中，不直接覆蓋原文。
     * @returns ApiResponse_TaskRead_ Successful Response
     * @throws ApiError
     */
    public static optimizeChapterScript({
        chapterId,
        requestBody,
    }: {
        chapterId: string,
        requestBody: ScriptTaskOptions,
    }): CancelablePromise<ApiResponse_TaskRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/chapters/{chapter_id}/optimize',
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
     * 提交一致性檢查任務
     * 檢查分鏡序列的敘事與視覺一致性問題。
     * @returns ApiResponse_TaskRead_ Successful Response
     * @throws ApiError
     */
    public static checkChapterConsistency({
        chapterId,
        requestBody,
    }: {
        chapterId: string,
        requestBody: ScriptTaskOptions,
    }): CancelablePromise<ApiResponse_TaskRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/chapters/{chapter_id}/consistency-check',
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
     * 提交分鏡提示詞生成任務
     * 為分鏡產生首尾幀圖片提示詞與影片提示詞。
     * @returns ApiResponse_TaskRead_ Successful Response
     * @throws ApiError
     */
    public static generateShotPrompts({
        shotId,
        requestBody,
    }: {
        shotId: string,
        requestBody: ScriptTaskOptions,
    }): CancelablePromise<ApiResponse_TaskRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/shots/{shot_id}/generate-prompts',
            path: {
                'shot_id': shotId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 提交分鏡關鍵幀圖片生成任務
     * 為分鏡的關鍵幀生成圖片。
     * @returns ApiResponse_TaskRead_ Successful Response
     * @throws ApiError
     */
    public static generateShotImages({
        shotId,
        requestBody,
    }: {
        shotId: string,
        requestBody: ImageTaskOptions,
    }): CancelablePromise<ApiResponse_TaskRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/shots/{shot_id}/generate-images',
            path: {
                'shot_id': shotId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 提交分鏡影片生成任務
     * 為分鏡生成影片。
     *
     * 不強制要求 video-readiness 通過 —— 使用者可能刻意先試跑。
     * 準備度資訊由 `GET /shots/{id}/readiness` 提供，由前端提示。
     * @returns ApiResponse_TaskRead_ Successful Response
     * @throws ApiError
     */
    public static generateShotVideo({
        shotId,
        requestBody,
    }: {
        shotId: string,
        requestBody: VideoTaskOptions,
    }): CancelablePromise<ApiResponse_TaskRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/shots/{shot_id}/generate-video',
            path: {
                'shot_id': shotId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 提交資產參考圖生成任務
     * 為角色／場景／道具／服裝生成參考圖。
     * @returns ApiResponse_TaskRead_ Successful Response
     * @throws ApiError
     */
    public static generateAssetImage({
        requestBody,
    }: {
        requestBody: AssetImageTaskOptions,
    }): CancelablePromise<ApiResponse_TaskRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/assets/generate-image',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 批次提交圖片生成
     * 為多個分鏡批次提交圖片生成。
     *
     * 每個分鏡各建一筆任務，讓進度與失敗可以個別追蹤與重試；
     * 找不到的分鏡會被略過並列在 `skipped`，不讓整批失敗。
     * @returns ApiResponse_BatchResult_ Successful Response
     * @throws ApiError
     */
    public static batchGenerateShotImages({
        requestBody,
    }: {
        requestBody: BatchShotRequest,
    }): CancelablePromise<ApiResponse_BatchResult_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/shots/batch/generate-images',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
