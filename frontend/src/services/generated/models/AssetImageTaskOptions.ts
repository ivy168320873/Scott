/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AssetKind } from './AssetKind';
/**
 * 資產參考圖生成選項。
 */
export type AssetImageTaskOptions = {
    /**
     * 資產類型
     */
    asset_kind: AssetKind;
    /**
     * 資產 ID
     */
    asset_id: string;
    /**
     * 指定圖片模型
     */
    model_id?: (string | null);
    /**
     * 生成張數
     */
    count?: number;
};

