/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ShotStatus } from './ShotStatus';
/**
 * 分鏡準備度。
 *
 * 刻意把三種狀態分開回報，避免前端把「已確認」誤當成「可以生成影片」：
 * - `status`：資訊提取確認狀態
 * - `video_ready`：是否具備影片生成條件
 */
export type ShotReadiness = {
    /**
     * 分鏡 ID
     */
    shot_id: string;
    /**
     * 資訊提取確認狀態
     */
    status: ShotStatus;
    /**
     * 尚未確認的資產候選數
     */
    pending_candidate_count: number;
    /**
     * 尚未確認的對白候選數
     */
    pending_dialogue_count: number;
    /**
     * 是否具備影片生成條件
     */
    video_ready: boolean;
    /**
     * 無法生成影片的原因；為空表示就緒
     */
    blocking_reasons: Array<string>;
};

