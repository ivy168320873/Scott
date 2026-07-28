/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AssetViewAngle } from './AssetViewAngle';
/**
 * 為資產新增參考圖。
 */
export type AssetImageCreate = {
    /**
     * 圖片檔案 ID
     */
    file_id: string;
    /**
     * 視角
     */
    view_angle?: AssetViewAngle;
    /**
     * 是否為主要參考圖
     */
    is_primary?: boolean;
    /**
     * 產生此圖所用的提示詞
     */
    prompt?: string;
};

