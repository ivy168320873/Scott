/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ShotCandidateStatus } from './ShotCandidateStatus';
import type { ShotCandidateType } from './ShotCandidateType';
/**
 * 資產提取候選對外表示。
 */
export type ShotCandidateRead = {
    /**
     * 候選 ID
     */
    id: string;
    /**
     * 所屬分鏡 ID
     */
    shot_id: string;
    /**
     * 候選資產類型
     */
    candidate_type: ShotCandidateType;
    /**
     * 提取出的名稱
     */
    name: string;
    /**
     * 提取出的描述
     */
    description: string;
    /**
     * 確認狀態
     */
    status: ShotCandidateStatus;
    /**
     * 確認後連結到的資產 ID
     */
    linked_entity_id: (string | null);
    /**
     * 建立時間
     */
    created_at: string;
    /**
     * 更新時間
     */
    updated_at: string;
};

