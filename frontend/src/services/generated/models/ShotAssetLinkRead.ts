/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AssetKind } from './AssetKind';
/**
 * 分鏡↔資產關聯對外表示。
 */
export type ShotAssetLinkRead = {
    /**
     * 關聯行 ID
     */
    id: number;
    /**
     * 分鏡 ID
     */
    shot_id: string;
    /**
     * 資產類型
     */
    asset_kind: AssetKind;
    /**
     * 資產 ID
     */
    asset_id: string;
    /**
     * 資產名稱（便於前端顯示）
     */
    asset_name?: string;
    /**
     * 出場順序
     */
    index: number;
    /**
     * 特殊說明
     */
    note: string;
};

