/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 單一相依元件的健康狀態。
 */
export type HealthComponent = {
    /**
     * 元件名稱
     */
    name: string;
    /**
     * 是否健康
     */
    healthy: boolean;
    /**
     * 補充說明（例如降級原因）
     */
    detail?: string;
};

