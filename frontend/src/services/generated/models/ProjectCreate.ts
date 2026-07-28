/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProjectVisualStyle } from './ProjectVisualStyle';
/**
 * 建立專案。
 */
export type ProjectCreate = {
    /**
     * 專案名稱
     */
    name: string;
    /**
     * 專案簡介
     */
    description?: string;
    /**
     * 題材
     */
    genre?: string;
    /**
     * 畫面表現形式
     */
    visual_style?: ProjectVisualStyle;
    /**
     * 專案級風格提示詞
     */
    style_prompt?: string;
    /**
     * 生成隨機種子；0 表示不指定
     */
    seed?: number;
    /**
     * 是否跨章節統一風格
     */
    unify_style?: boolean;
    /**
     * 預設影片比例
     */
    default_video_ratio?: string;
};

