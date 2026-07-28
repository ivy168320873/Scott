/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FileUsageKind } from './FileUsageKind';
/**
 * 媒體用途對外表示。
 */
export type MediaUsageRead = {
    /**
     * 用途記錄 ID
     */
    id: number;
    /**
     * 檔案 ID
     */
    file_id: string;
    /**
     * 用途類型
     */
    usage_kind: FileUsageKind;
    /**
     * 擁有者類型
     */
    owner_type: string;
    /**
     * 擁有者 ID
     */
    owner_id: string;
    /**
     * 補充說明
     */
    note: string;
};

