/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 建立任務產物關聯。
 */
export type TaskLinkCreate = {
    /**
     * 產出資源類型
     */
    resource_type?: string;
    /**
     * 業務類型
     */
    relation_type?: string;
    /**
     * 業務實體 ID
     */
    relation_entity_id?: string;
    /**
     * 產出檔案 ID
     */
    file_id?: (string | null);
};

