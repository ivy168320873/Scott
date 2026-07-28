/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CameraAngle } from './CameraAngle';
import type { CameraMovement } from './CameraMovement';
import type { CameraShotType } from './CameraShotType';
import type { VFXType } from './VFXType';
/**
 * 分鏡細節對外表示。
 */
export type ShotDetailRead = {
    /**
     * 景別
     */
    camera_shot: CameraShotType;
    /**
     * 機位角度
     */
    angle: CameraAngle;
    /**
     * 運鏡方式
     */
    movement: CameraMovement;
    /**
     * 關聯場景 ID
     */
    scene_id: (string | null);
    /**
     * 鏡頭時長（秒）
     */
    duration_seconds: number;
    /**
     * 影片比例覆蓋
     */
    override_video_ratio: (string | null);
    /**
     * 鏡頭整體描述
     */
    description: string;
    /**
     * 動作節拍
     */
    action_beats: Array<string>;
    /**
     * 情緒標籤
     */
    mood_tags: Array<string>;
    /**
     * 氛圍描述
     */
    atmosphere: string;
    /**
     * 視效類型
     */
    vfx_type: VFXType;
    /**
     * 視效說明
     */
    vfx_note: string;
    /**
     * 是否包含 BGM
     */
    has_bgm: boolean;
    /**
     * 影片生成提示詞
     */
    video_prompt: string;
};

