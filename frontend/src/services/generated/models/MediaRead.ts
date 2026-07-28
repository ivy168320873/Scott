/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FileType } from './FileType';
/**
 * 媒體檔案對外表示。
 */
export type MediaRead = {
    /**
     * 檔案 ID
     */
    id: string;
    /**
     * 檔案類型
     */
    file_type: FileType;
    /**
     * 原始檔名
     */
    filename: string;
    /**
     * MIME type
     */
    content_type: string;
    /**
     * 檔案大小
     */
    size_bytes: number;
    /**
     * 影像寬度
     */
    width: number;
    /**
     * 影像高度
     */
    height: number;
    /**
     * 影音長度
     */
    duration_seconds: number;
    /**
     * 所屬專案 ID
     */
    project_id: (string | null);
    /**
     * 產生此檔案的任務 ID
     */
    source_task_id: (string | null);
    /**
     * 可讀取的 URL；由儲存後端於執行期組出
     */
    url: string;
    /**
     * 額外中繼資料
     */
    file_metadata: Record<string, any>;
    /**
     * 建立時間
     */
    created_at: string;
    /**
     * 更新時間
     */
    updated_at: string;
};

