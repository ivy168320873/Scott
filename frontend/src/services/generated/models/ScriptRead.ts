/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 章節腳本內容。
 */
export type ScriptRead = {
    /**
     * 章節 ID
     */
    chapter_id: string;
    /**
     * 腳本原文
     */
    raw_text: string;
    /**
     * 模型精簡後的腳本
     */
    condensed_text: string;
    /**
     * 原文字數
     */
    raw_length: number;
    /**
     * 精簡後字數
     */
    condensed_length: number;
};

