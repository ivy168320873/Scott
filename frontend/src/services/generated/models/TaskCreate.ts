/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TaskDeliveryMode } from './TaskDeliveryMode';
import type { TaskKind } from './TaskKind';
/**
 * 建立生成任務。
 *
 * Phase 3 只負責建立與查詢任務紀錄；實際排程與執行於 Phase 4 接上。
 */
export type TaskCreate = {
    /**
     * 業務任務類型
     */
    task_kind: TaskKind;
    /**
     * 交付方式
     */
    mode?: TaskDeliveryMode;
    /**
     * 請求參數
     */
    payload?: Record<string, any>;
    /**
     * 關聯專案 ID
     */
    project_id?: (string | null);
    /**
     * 關聯章節 ID
     */
    chapter_id?: (string | null);
    /**
     * 關聯分鏡 ID
     */
    shot_id?: (string | null);
    /**
     * 指定供應商 ID
     */
    provider_id?: (string | null);
    /**
     * 指定模型 ID
     */
    model_id?: (string | null);
    /**
     * 最大重試次數
     */
    max_retries?: number;
};

