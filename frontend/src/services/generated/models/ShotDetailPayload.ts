/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CameraAngle } from './CameraAngle';
import type { CameraMovement } from './CameraMovement';
import type { CameraShotType } from './CameraShotType';
import type { VFXType } from './VFXType';
/**
 * 分鏡拍攝細節（建立與更新共用）。
 */
export type ShotDetailPayload = {
    /**
     * 景別
     */
    camera_shot?: (CameraShotType | null);
    /**
     * 機位角度
     */
    angle?: (CameraAngle | null);
    /**
     * 運鏡方式
     */
    movement?: (CameraMovement | null);
    /**
     * 關聯場景 ID
     */
    scene_id?: (string | null);
    /**
     * 鏡頭時長（秒）
     */
    duration_seconds?: (number | null);
    /**
     * 影片比例覆蓋
     */
    override_video_ratio?: (string | null);
    /**
     * 鏡頭整體描述
     */
    description?: (string | null);
    /**
     * 動作節拍
     */
    action_beats?: (Array<string> | null);
    /**
     * 情緒標籤
     */
    mood_tags?: (Array<string> | null);
    /**
     * 氛圍描述
     */
    atmosphere?: (string | null);
    /**
     * 視效類型
     */
    vfx_type?: (VFXType | null);
    /**
     * 視效說明
     */
    vfx_note?: (string | null);
    /**
     * 是否包含 BGM
     */
    has_bgm?: (boolean | null);
    /**
     * 影片生成提示詞
     */
    video_prompt?: (string | null);
};

