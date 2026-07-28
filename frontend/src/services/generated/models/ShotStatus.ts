/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 鏡頭資訊提取確認狀態。
 *
 * - `pending`：仍有提取確認工作未完成。
 * - `ready`：已完成提取確認，可進入生成準備。
 *
 * 刻意不含 `generating`：執行時狀態屬於任務系統的職責。
 */
export type ShotStatus = 'pending' | 'ready';
