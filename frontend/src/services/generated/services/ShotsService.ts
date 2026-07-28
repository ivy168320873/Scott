/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_list_ShotAssetLinkRead__ } from '../models/ApiResponse_list_ShotAssetLinkRead__';
import type { ApiResponse_list_ShotCandidateRead__ } from '../models/ApiResponse_list_ShotCandidateRead__';
import type { ApiResponse_list_ShotDialogueCandidateRead__ } from '../models/ApiResponse_list_ShotDialogueCandidateRead__';
import type { ApiResponse_list_ShotDialogueRead__ } from '../models/ApiResponse_list_ShotDialogueRead__';
import type { ApiResponse_list_ShotFrameRead__ } from '../models/ApiResponse_list_ShotFrameRead__';
import type { ApiResponse_OkData_ } from '../models/ApiResponse_OkData_';
import type { ApiResponse_Page_ShotRead__ } from '../models/ApiResponse_Page_ShotRead__';
import type { ApiResponse_ShotAssetLinkRead_ } from '../models/ApiResponse_ShotAssetLinkRead_';
import type { ApiResponse_ShotCandidateRead_ } from '../models/ApiResponse_ShotCandidateRead_';
import type { ApiResponse_ShotDialogueCandidateRead_ } from '../models/ApiResponse_ShotDialogueCandidateRead_';
import type { ApiResponse_ShotDialogueRead_ } from '../models/ApiResponse_ShotDialogueRead_';
import type { ApiResponse_ShotFrameRead_ } from '../models/ApiResponse_ShotFrameRead_';
import type { ApiResponse_ShotRead_ } from '../models/ApiResponse_ShotRead_';
import type { ApiResponse_ShotReadiness_ } from '../models/ApiResponse_ShotReadiness_';
import type { AssetKind } from '../models/AssetKind';
import type { CandidateResolve } from '../models/CandidateResolve';
import type { DialogueCandidateResolve } from '../models/DialogueCandidateResolve';
import type { ShotAssetLinkCreate } from '../models/ShotAssetLinkCreate';
import type { ShotCandidateStatus } from '../models/ShotCandidateStatus';
import type { ShotCreate } from '../models/ShotCreate';
import type { ShotDialogueCreate } from '../models/ShotDialogueCreate';
import type { ShotDialogueUpdate } from '../models/ShotDialogueUpdate';
import type { ShotFrameCreate } from '../models/ShotFrameCreate';
import type { ShotFrameUpdate } from '../models/ShotFrameUpdate';
import type { ShotStatus } from '../models/ShotStatus';
import type { ShotUpdate } from '../models/ShotUpdate';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ShotsService {
    /**
     * 列出分鏡
     * 分頁列出某章節的分鏡（依鏡頭序號遞增）。
     * @returns ApiResponse_Page_ShotRead__ Successful Response
     * @throws ApiError
     */
    public static listShots({
        chapterId,
        search,
        status,
        page = 1,
        pageSize = 20,
    }: {
        chapterId: string,
        /**
         * 以標題、腳本段落做模糊搜尋
         */
        search?: (string | null),
        /**
         * 依提取確認狀態過濾
         */
        status?: (ShotStatus | null),
        /**
         * 頁碼，從 1 開始
         */
        page?: number,
        /**
         * 每頁筆數
         */
        pageSize?: number,
    }): CancelablePromise<ApiResponse_Page_ShotRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/chapters/{chapter_id}/shots',
            path: {
                'chapter_id': chapterId,
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
     * 建立分鏡
     * 建立分鏡；序號留空時自動接續。
     * @returns ApiResponse_ShotRead_ Successful Response
     * @throws ApiError
     */
    public static createShot({
        chapterId,
        requestBody,
    }: {
        chapterId: string,
        requestBody: ShotCreate,
    }): CancelablePromise<ApiResponse_ShotRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/chapters/{chapter_id}/shots',
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
     * 取得分鏡
     * 取得單一分鏡。
     * @returns ApiResponse_ShotRead_ Successful Response
     * @throws ApiError
     */
    public static getShot({
        shotId,
    }: {
        shotId: string,
    }): CancelablePromise<ApiResponse_ShotRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/shots/{shot_id}',
            path: {
                'shot_id': shotId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 更新分鏡
     * 更新分鏡；未傳入的欄位保留原值。狀態不可直接寫入。
     * @returns ApiResponse_ShotRead_ Successful Response
     * @throws ApiError
     */
    public static updateShot({
        shotId,
        requestBody,
    }: {
        shotId: string,
        requestBody: ShotUpdate,
    }): CancelablePromise<ApiResponse_ShotRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/studio/shots/{shot_id}',
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
     * 刪除分鏡
     * 刪除分鏡及其子資源。
     * @returns ApiResponse_OkData_ Successful Response
     * @throws ApiError
     */
    public static deleteShot({
        shotId,
    }: {
        shotId: string,
    }): CancelablePromise<ApiResponse_OkData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/studio/shots/{shot_id}',
            path: {
                'shot_id': shotId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 重算分鏡確認狀態
     * 依提取確認進度重算分鏡狀態。
     * @returns ApiResponse_ShotRead_ Successful Response
     * @throws ApiError
     */
    public static recomputeShotStatus({
        shotId,
    }: {
        shotId: string,
    }): CancelablePromise<ApiResponse_ShotRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/shots/{shot_id}/recompute-status',
            path: {
                'shot_id': shotId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 取得分鏡準備度
     * 回報分鏡的確認狀態與影片生成準備度（兩者分離）。
     * @returns ApiResponse_ShotReadiness_ Successful Response
     * @throws ApiError
     */
    public static getShotReadiness({
        shotId,
    }: {
        shotId: string,
    }): CancelablePromise<ApiResponse_ShotReadiness_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/shots/{shot_id}/readiness',
            path: {
                'shot_id': shotId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 標記分鏡已完成提取
     * 標記分鏡已完成一次提取並重算狀態。
     * @returns ApiResponse_ShotRead_ Successful Response
     * @throws ApiError
     */
    public static markShotExtracted({
        shotId,
    }: {
        shotId: string,
    }): CancelablePromise<ApiResponse_ShotRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/shots/{shot_id}/mark-extracted',
            path: {
                'shot_id': shotId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 列出關鍵幀
     * 列出分鏡的所有關鍵幀。
     * @returns ApiResponse_list_ShotFrameRead__ Successful Response
     * @throws ApiError
     */
    public static listShotFrames({
        shotId,
    }: {
        shotId: string,
    }): CancelablePromise<ApiResponse_list_ShotFrameRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/shots/{shot_id}/frames',
            path: {
                'shot_id': shotId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 新增關鍵幀
     * 為分鏡新增關鍵幀。
     * @returns ApiResponse_ShotFrameRead_ Successful Response
     * @throws ApiError
     */
    public static createShotFrame({
        shotId,
        requestBody,
    }: {
        shotId: string,
        requestBody: ShotFrameCreate,
    }): CancelablePromise<ApiResponse_ShotFrameRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/shots/{shot_id}/frames',
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
     * 更新關鍵幀
     * 更新關鍵幀；未傳入的欄位保留原值。
     * @returns ApiResponse_ShotFrameRead_ Successful Response
     * @throws ApiError
     */
    public static updateShotFrame({
        frameId,
        requestBody,
    }: {
        frameId: string,
        requestBody: ShotFrameUpdate,
    }): CancelablePromise<ApiResponse_ShotFrameRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/studio/frames/{frame_id}',
            path: {
                'frame_id': frameId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 刪除關鍵幀
     * 刪除關鍵幀。
     * @returns ApiResponse_OkData_ Successful Response
     * @throws ApiError
     */
    public static deleteShotFrame({
        frameId,
    }: {
        frameId: string,
    }): CancelablePromise<ApiResponse_OkData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/studio/frames/{frame_id}',
            path: {
                'frame_id': frameId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 列出對白
     * 列出分鏡的對白（依順序）。
     * @returns ApiResponse_list_ShotDialogueRead__ Successful Response
     * @throws ApiError
     */
    public static listShotDialogues({
        shotId,
    }: {
        shotId: string,
    }): CancelablePromise<ApiResponse_list_ShotDialogueRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/shots/{shot_id}/dialogues',
            path: {
                'shot_id': shotId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 新增對白
     * 新增對白；順序留空時附加於最後。
     * @returns ApiResponse_ShotDialogueRead_ Successful Response
     * @throws ApiError
     */
    public static createShotDialogue({
        shotId,
        requestBody,
    }: {
        shotId: string,
        requestBody: ShotDialogueCreate,
    }): CancelablePromise<ApiResponse_ShotDialogueRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/shots/{shot_id}/dialogues',
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
     * 更新對白
     * 更新對白；未傳入的欄位保留原值。
     * @returns ApiResponse_ShotDialogueRead_ Successful Response
     * @throws ApiError
     */
    public static updateShotDialogue({
        dialogueId,
        requestBody,
    }: {
        dialogueId: string,
        requestBody: ShotDialogueUpdate,
    }): CancelablePromise<ApiResponse_ShotDialogueRead_> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/api/v1/studio/dialogues/{dialogue_id}',
            path: {
                'dialogue_id': dialogueId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 刪除對白
     * 刪除對白。
     * @returns ApiResponse_OkData_ Successful Response
     * @throws ApiError
     */
    public static deleteShotDialogue({
        dialogueId,
    }: {
        dialogueId: string,
    }): CancelablePromise<ApiResponse_OkData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/studio/dialogues/{dialogue_id}',
            path: {
                'dialogue_id': dialogueId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 列出資產提取候選
     * 列出分鏡的資產提取候選。
     * @returns ApiResponse_list_ShotCandidateRead__ Successful Response
     * @throws ApiError
     */
    public static listShotCandidates({
        shotId,
        status,
    }: {
        shotId: string,
        status?: (ShotCandidateStatus | null),
    }): CancelablePromise<ApiResponse_list_ShotCandidateRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/shots/{shot_id}/candidates',
            path: {
                'shot_id': shotId,
            },
            query: {
                'status': status,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 確認或忽略資產候選
     * 確認（連結既有資產）或忽略一筆候選。
     * @returns ApiResponse_ShotCandidateRead_ Successful Response
     * @throws ApiError
     */
    public static resolveShotCandidate({
        candidateId,
        requestBody,
    }: {
        candidateId: string,
        requestBody: CandidateResolve,
    }): CancelablePromise<ApiResponse_ShotCandidateRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/candidates/{candidate_id}/resolve',
            path: {
                'candidate_id': candidateId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 列出對白提取候選
     * 列出分鏡的對白提取候選。
     * @returns ApiResponse_list_ShotDialogueCandidateRead__ Successful Response
     * @throws ApiError
     */
    public static listShotDialogueCandidates({
        shotId,
    }: {
        shotId: string,
    }): CancelablePromise<ApiResponse_list_ShotDialogueCandidateRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/shots/{shot_id}/dialogue-candidates',
            path: {
                'shot_id': shotId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 接受或忽略對白候選
     * 接受（轉為正式對白）或忽略一筆對白候選。
     * @returns ApiResponse_ShotDialogueCandidateRead_ Successful Response
     * @throws ApiError
     */
    public static resolveShotDialogueCandidate({
        candidateId,
        requestBody,
    }: {
        candidateId: string,
        requestBody: DialogueCandidateResolve,
    }): CancelablePromise<ApiResponse_ShotDialogueCandidateRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/dialogue-candidates/{candidate_id}/resolve',
            path: {
                'candidate_id': candidateId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 列出分鏡引用的資產
     * 列出分鏡引用的角色／場景／道具／服裝。
     * @returns ApiResponse_list_ShotAssetLinkRead__ Successful Response
     * @throws ApiError
     */
    public static listShotAssets({
        shotId,
        assetKind,
    }: {
        shotId: string,
        /**
         * 依資產類型過濾
         */
        assetKind?: (AssetKind | null),
    }): CancelablePromise<ApiResponse_list_ShotAssetLinkRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/shots/{shot_id}/assets',
            path: {
                'shot_id': shotId,
            },
            query: {
                'asset_kind': assetKind,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 關聯資產到分鏡
     * 把資產關聯到分鏡。
     * @returns ApiResponse_ShotAssetLinkRead_ Successful Response
     * @throws ApiError
     */
    public static linkShotAsset({
        shotId,
        requestBody,
    }: {
        shotId: string,
        requestBody: ShotAssetLinkCreate,
    }): CancelablePromise<ApiResponse_ShotAssetLinkRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/shots/{shot_id}/assets',
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
     * 移除分鏡資產關聯
     * 移除分鏡與資產的關聯。
     * @returns ApiResponse_OkData_ Successful Response
     * @throws ApiError
     */
    public static unlinkShotAsset({
        shotId,
        assetKind,
        assetId,
    }: {
        shotId: string,
        assetKind: AssetKind,
        assetId: string,
    }): CancelablePromise<ApiResponse_OkData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/studio/shots/{shot_id}/assets/{asset_kind}/{asset_id}',
            path: {
                'shot_id': shotId,
                'asset_kind': assetKind,
                'asset_id': assetId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
