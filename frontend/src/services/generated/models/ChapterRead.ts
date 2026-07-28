/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChapterStatus } from './ChapterStatus';
/**
 * 章節對外表示。
 *
 * 刻意不含腳本全文：章節列表可能有數十筆，每筆夾帶完整腳本會讓
 * 回應體積失控。腳本請用 `GET /chapters/{id}/script`。
 */
export type ChapterRead = {
    /**
     * 章節 ID
     */
    id: string;
    /**
     * 所屬專案 ID
     */
    project_id: string;
    /**
     * 章節序號
     */
    index: number;
    /**
     * 章節標題
     */
    title: string;
    /**
     * 章節摘要
     */
    summary: string;
    /**
     * 章節狀態
     */
    status: ChapterStatus;
    /**
     * 分鏡數量
     */
    shot_count: number;
    /**
     * 是否已輸入腳本
     */
    has_script?: boolean;
    /**
     * 建立時間
     */
    created_at: string;
    /**
     * 更新時間
     */
    updated_at: string;
};

