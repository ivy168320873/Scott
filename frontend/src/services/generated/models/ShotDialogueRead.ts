/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DialogueLineMode } from './DialogueLineMode';
/**
 * 對白對外表示。
 */
export type ShotDialogueRead = {
    /**
     * 對白 ID
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
     * 說話角色 ID
     */
    character_id: (string | null);
    /**
     * 說話者名稱
     */
    speaker_name: string;
    /**
     * 對白內容
     */
    content: string;
    /**
     * 對白模式
     */
    mode: DialogueLineMode;
    /**
     * 情緒提示
     */
    emotion: string;
    /**
     * 建立時間
     */
    created_at: string;
    /**
     * 更新時間
     */
    updated_at: string;
};

