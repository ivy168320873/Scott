/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AssetKind } from './AssetKind';
import type { AssetViewAngle } from './AssetViewAngle';
/**
 * 資產圖片對外表示。
 */
export type AssetImageRead = {
    /**
     * 資產圖片 ID
     */
    id: string;
    /**
     * 資產類型
     */
    asset_kind: AssetKind;
    /**
     * 資產 ID
     */
    asset_id: string;
    /**
     * 圖片檔案 ID
     */
    file_id: string;
    /**
     * 視角
     */
    view_angle: AssetViewAngle;
    /**
     * 是否為主要參考圖
     */
    is_primary: boolean;
    /**
     * 提示詞
     */
    prompt: string;
    /**
     * 建立時間
     */
    created_at: string;
};

