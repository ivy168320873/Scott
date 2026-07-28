/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DialogueLineMode } from './DialogueLineMode';
/**
 * 更新對白；未傳入的欄位保留原值。
 */
export type ShotDialogueUpdate = {
    content?: (string | null);
    character_id?: (string | null);
    speaker_name?: (string | null);
    mode?: (DialogueLineMode | null);
    emotion?: (string | null);
};

