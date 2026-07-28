/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TaskDeliveryMode } from './TaskDeliveryMode';
import type { TaskStatus } from './TaskStatus';
/**
 * 任務對外表示。
 */
export type TaskRead = {
    /**
     * 任務 ID
     */
    id: string;
    /**
     * 業務任務類型
     */
    task_kind: string;
    /**
     * 交付方式
     */
    mode: TaskDeliveryMode;
    /**
     * 任務狀態
     */
    status: TaskStatus;
    /**
     * 進度 0-100
     */
    progress: number;
    /**
     * 目前步驟描述
     */
    progress_message: string;
    /**
     * 請求參數
     */
    payload: Record<string, any>;
    /**
     * 執行結果
     */
    result: (Record<string, any> | null);
    /**
     * 失敗原因
     */
    error: string;
    /**
     * 標準化錯誤碼
     */
    error_code: string;
    /**
     * 關聯專案 ID
     */
    project_id: (string | null);
    /**
     * 關聯章節 ID
     */
    chapter_id: (string | null);
    /**
     * 關聯分鏡 ID
     */
    shot_id: (string | null);
    /**
     * 供應商 ID
     */
    provider_id: (string | null);
    /**
     * 模型 ID
     */
    model_id: (string | null);
    /**
     * 是否已請求取消
     */
    cancel_requested: boolean;
    /**
     * 取消原因
     */
    cancel_reason: string;
    /**
     * 開始執行時間
     */
    started_at: (string | null);
    /**
     * 結束時間
     */
    finished_at: (string | null);
    /**
     * 耗時（秒）；未開始為 null
     */
    elapsed_seconds: (number | null);
    /**
     * 目前是否可取消
     */
    is_cancellable: boolean;
    /**
     * 已重試次數
     */
    retry_count: number;
    /**
     * 最大重試次數
     */
    max_retries: number;
    /**
     * 建立時間
     */
    created_at: string;
    /**
     * 更新時間
     */
    updated_at: string;
};

