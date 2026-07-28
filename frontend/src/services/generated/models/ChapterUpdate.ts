/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChapterStatus } from './ChapterStatus';
/**
 * 更新章節；未傳入的欄位保留原值。
 */
export type ChapterUpdate = {
    title?: (string | null);
    index?: (number | null);
    summary?: (string | null);
    status?: (ChapterStatus | null);
};

