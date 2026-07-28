/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ShotDetailPayload } from './ShotDetailPayload';
/**
 * 更新分鏡；未傳入的欄位保留原值。
 *
 * 刻意不開放直接寫入 `status`：分鏡狀態由提取確認進度推導，
 * 請改用 `POST /shots/{id}/recompute-status`，避免狀態與實際資料脫節。
 */
export type ShotUpdate = {
    title?: (string | null);
    index?: (number | null);
    script_excerpt?: (string | null);
    /**
     * 是否明確跳過提取
     */
    skip_extraction?: (boolean | null);
    detail?: (ShotDetailPayload | null);
};

