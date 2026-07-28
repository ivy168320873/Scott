/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TaskLinkStatus } from './TaskLinkStatus';
/**
 * 任務產物關聯對外表示。
 */
export type TaskLinkRead = {
    /**
     * 關聯行 ID
     */
    id: number;
    /**
     * 任務 ID
     */
    task_id: string;
    /**
     * 產出資源類型
     */
    resource_type: string;
    /**
     * 業務類型
     */
    relation_type: string;
    /**
     * 業務實體 ID
     */
    relation_entity_id: string;
    /**
     * 產出檔案 ID
     */
    file_id: (string | null);
    /**
     * 採用狀態
     */
    status: TaskLinkStatus;
    /**
     * 建立時間
     */
    created_at: string;
    /**
     * 更新時間
     */
    updated_at: string;
};

