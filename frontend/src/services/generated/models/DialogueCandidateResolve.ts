/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 接受或忽略一筆對白候選。
 */
export type DialogueCandidateResolve = {
    /**
     * `accept` 接受並轉為正式對白，或 `ignore` 忽略
     */
    action: string;
    /**
     * action=accept 時可指定說話角色
     */
    character_id?: (string | null);
};

