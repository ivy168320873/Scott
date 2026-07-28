/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 生成任務狀態。
 *
 * 終態為 `succeeded` / `failed` / `cancelled`，其餘為進行中。
 */
export type TaskStatus = 'pending' | 'running' | 'streaming' | 'succeeded' | 'failed' | 'cancelled';
