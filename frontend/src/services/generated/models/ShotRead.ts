/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ShotDetailRead } from './ShotDetailRead';
import type { ShotStatus } from './ShotStatus';
/**
 * 分鏡對外表示。
 */
export type ShotRead = {
    /**
     * 分鏡 ID
     */
    id: string;
    /**
     * 所屬章節 ID
     */
    chapter_id: string;
    /**
     * 鏡頭序號
     */
    index: number;
    /**
     * 鏡頭標題
     */
    title: string;
    /**
     * 對應的腳本段落
     */
    script_excerpt: string;
    /**
     * 資訊提取確認狀態（非執行時狀態）
     */
    status: ShotStatus;
    /**
     * 是否跳過提取
     */
    skip_extraction: boolean;
    /**
     * 最近一次完成提取的時間
     */
    last_extracted_at: (string | null);
    /**
     * 縮圖檔案 ID
     */
    thumbnail_file_id: (string | null);
    /**
     * 已採用的成品影片檔案 ID
     */
    generated_video_file_id: (string | null);
    /**
     * 拍攝細節
     */
    detail?: (ShotDetailRead | null);
    /**
     * 建立時間
     */
    created_at: string;
    /**
     * 更新時間
     */
    updated_at: string;
};

