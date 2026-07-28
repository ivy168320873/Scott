/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 更新章節腳本。
 *
 * 只允許更新 `raw_text`：`condensed_text` 是 AI 產物，
 * 由腳本處理任務寫入，不開放人工直接改寫，否則兩者會失去對應關係。
 */
export type ScriptUpdate = {
    /**
     * 腳本原文
     */
    raw_text: string;
};

