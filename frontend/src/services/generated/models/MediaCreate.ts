/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FileType } from './FileType';
/**
 * 登記一筆媒體檔案。
 *
 * 僅建立中繼資料紀錄；實際二進位內容由上傳端點寫入物件儲存。
 */
export type MediaCreate = {
    /**
     * 檔案類型
     */
    file_type: FileType;
    /**
     * 物件儲存 key
     */
    storage_key: string;
    /**
     * 原始檔名
     */
    filename?: string;
    /**
     * MIME type
     */
    content_type?: string;
    /**
     * 檔案大小
     */
    size_bytes?: number;
    /**
     * 影像寬度
     */
    width?: number;
    /**
     * 影像高度
     */
    height?: number;
    /**
     * 影音長度
     */
    duration_seconds?: number;
    /**
     * 所屬專案 ID
     */
    project_id?: (string | null);
    /**
     * 額外中繼資料
     */
    file_metadata?: Record<string, any>;
};

