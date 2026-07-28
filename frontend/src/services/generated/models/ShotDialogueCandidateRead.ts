/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DialogueLineMode } from './DialogueLineMode';
import type { ShotDialogueCandidateStatus } from './ShotDialogueCandidateStatus';
/**
 * 對白提取候選對外表示。
 */
export type ShotDialogueCandidateRead = {
    /**
     * 對白候選 ID
     */
    id: string;
    /**
     * 所屬分鏡 ID
     */
    shot_id: string;
    /**
     * 順序
     */
    index: number;
    /**
     * 提取出的說話者
     */
    speaker_name: string;
    /**
     * 提取出的對白內容
     */
    content: string;
    /**
     * 對白模式
     */
    mode: DialogueLineMode;
    /**
     * 確認狀態
     */
    status: ShotDialogueCandidateStatus;
    /**
     * 建立時間
     */
    created_at: string;
    /**
     * 更新時間
     */
    updated_at: string;
};

