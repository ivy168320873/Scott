/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ShotFrameType } from './ShotFrameType';
/**
 * 建立關鍵幀。
 */
export type ShotFrameCreate = {
    /**
     * 幀類型
     */
    frame_type: ShotFrameType;
    /**
     * 同類型內的序號
     */
    index?: number;
    /**
     * 該幀的圖片生成提示詞
     */
    prompt?: string;
    /**
     * 已採用的圖片檔案 ID
     */
    file_id?: (string | null);
};

