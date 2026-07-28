/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProjectStatus } from './ProjectStatus';
import type { ProjectVisualStyle } from './ProjectVisualStyle';
/**
 * 專案對外表示。
 */
export type ProjectRead = {
    /**
     * 專案 ID
     */
    id: string;
    /**
     * 專案名稱
     */
    name: string;
    /**
     * 專案簡介
     */
    description: string;
    /**
     * 題材
     */
    genre: string;
    /**
     * 畫面表現形式
     */
    visual_style: ProjectVisualStyle;
    /**
     * 專案級風格提示詞
     */
    style_prompt: string;
    /**
     * 專案狀態
     */
    status: ProjectStatus;
    /**
     * 生成隨機種子
     */
    seed: number;
    /**
     * 是否統一風格
     */
    unify_style: boolean;
    /**
     * 預設影片比例
     */
    default_video_ratio: string;
    /**
     * 封面圖檔案 ID
     */
    cover_file_id: (string | null);
    /**
     * 聚合統計
     */
    stats: Record<string, any>;
    /**
     * 建立時間
     */
    created_at: string;
    /**
     * 更新時間
     */
    updated_at: string;
};

