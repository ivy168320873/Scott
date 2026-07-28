/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 角色對外表示。
 */
export type CharacterRead = {
    /**
     * 資產 ID
     */
    id: string;
    /**
     * 資產名稱
     */
    name: string;
    /**
     * 描述
     */
    description: string;
    /**
     * 外觀提示詞
     */
    appearance_prompt: string;
    /**
     * 結構化屬性
     */
    attributes: Record<string, any>;
    /**
     * 建立時間
     */
    created_at: string;
    /**
     * 更新時間
     */
    updated_at: string;
    /**
     * 所屬專案 ID
     */
    project_id: string;
    /**
     * 綁定的演員 ID
     */
    actor_id: (string | null);
    /**
     * 角色定位
     */
    role_type: string;
    /**
     * 性格描述
     */
    personality: string;
};

