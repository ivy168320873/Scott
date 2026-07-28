/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AssetKind } from './AssetKind';
/**
 * 建立分鏡↔資產關聯。
 */
export type ShotAssetLinkCreate = {
    /**
     * 資產類型
     */
    asset_kind: AssetKind;
    /**
     * 資產 ID
     */
    asset_id: string;
    /**
     * 出場順序
     */
    index?: number;
    /**
     * 此鏡頭中的特殊說明
     */
    note?: string;
};

