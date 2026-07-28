/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 確認或忽略一筆資產候選。
 */
export type CandidateResolve = {
    /**
     * `link` 連結到既有資產，或 `ignore` 忽略
     */
    action: string;
    /**
     * action=link 時必填：要連結的資產 ID
     */
    linked_entity_id?: (string | null);
};

