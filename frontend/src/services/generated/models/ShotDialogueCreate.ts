/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DialogueLineMode } from './DialogueLineMode';
/**
 * 建立對白。
 */
export type ShotDialogueCreate = {
    /**
     * 對白內容
     */
    content: string;
    /**
     * 順序；留空自動附加於最後
     */
    index?: (number | null);
    /**
     * 說話角色 ID
     */
    character_id?: (string | null);
    /**
     * 說話者名稱
     */
    speaker_name?: string;
    /**
     * 對白模式
     */
    mode?: DialogueLineMode;
    /**
     * 情緒提示
     */
    emotion?: string;
};

