/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 建立角色。
 */
export type CharacterCreate = {
    /**
     * 資產名稱
     */
    name: string;
    /**
     * 描述
     */
    description?: string;
    /**
     * 外觀提示詞；生成時附加於鏡頭提示詞以維持一致性
     */
    appearance_prompt?: string;
    /**
     * 結構化屬性
     */
    attributes?: Record<string, any>;
    /**
     * 綁定的演員 ID
     */
    actor_id?: (string | null);
    /**
     * 角色定位
     */
    role_type?: string;
    /**
     * 性格描述
     */
    personality?: string;
};

