/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ShotDetailPayload } from './ShotDetailPayload';
/**
 * 建立分鏡；`index` 留空時自動指派為章節內下一個序號。
 */
export type ShotCreate = {
    /**
     * 鏡頭標題
     */
    title?: string;
    /**
     * 鏡頭序號；留空自動指派
     */
    index?: (number | null);
    /**
     * 對應的腳本段落
     */
    script_excerpt?: string;
    /**
     * 拍攝細節
     */
    detail?: (ShotDetailPayload | null);
};

