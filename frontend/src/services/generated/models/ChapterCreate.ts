/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 建立章節。
 *
 * `index` 留空時由 Service 自動指派為專案內的下一個序號。
 */
export type ChapterCreate = {
    /**
     * 章節標題
     */
    title: string;
    /**
     * 章節序號；留空自動指派
     */
    index?: (number | null);
    /**
     * 章節摘要
     */
    summary?: string;
    /**
     * 腳本原文
     */
    raw_text?: string;
};

