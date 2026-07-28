/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HealthComponent } from './HealthComponent';
/**
 * health endpoint 回應。
 */
export type HealthData = {
    /**
     * 整體狀態：ok / degraded
     */
    status: string;
    /**
     * 應用名稱
     */
    app: string;
    /**
     * 目前任務執行模式
     */
    task_mode: string;
    /**
     * 目前物件儲存後端
     */
    storage_backend: string;
    /**
     * 各相依元件狀態
     */
    components?: Array<HealthComponent>;
};

