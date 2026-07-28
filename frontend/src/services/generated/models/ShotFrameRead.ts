/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ShotFrameType } from './ShotFrameType';
/**
 * 關鍵幀對外表示。
 */
export type ShotFrameRead = {
    /**
     * 幀 ID
     */
    id: string;
    /**
     * 所屬分鏡 ID
     */
    shot_id: string;
    /**
     * 幀類型
     */
    frame_type: ShotFrameType;
    /**
     * 序號
     */
    index: number;
    /**
     * 圖片生成提示詞
     */
    prompt: string;
    /**
     * 圖片檔案 ID
     */
    file_id: (string | null);
    /**
     * 建立時間
     */
    created_at: string;
    /**
     * 更新時間
     */
    updated_at: string;
};

