/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProjectStatus } from './ProjectStatus';
import type { ProjectVisualStyle } from './ProjectVisualStyle';
/**
 * 更新專案；未傳入的欄位保留原值。
 */
export type ProjectUpdate = {
    name?: (string | null);
    description?: (string | null);
    genre?: (string | null);
    visual_style?: (ProjectVisualStyle | null);
    style_prompt?: (string | null);
    seed?: (number | null);
    unify_style?: (boolean | null);
    default_video_ratio?: (string | null);
    status?: (ProjectStatus | null);
    cover_file_id?: (string | null);
};

