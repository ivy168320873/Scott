/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 更新媒體中繼資料；未傳入的欄位保留原值。
 *
 * 刻意不允許改寫 `storage_key`：那等同把紀錄指向另一個實體檔案，
 * 會讓既有引用悄悄失效。需要換檔請建立新紀錄。
 */
export type MediaUpdate = {
    filename?: (string | null);
    project_id?: (string | null);
    file_metadata?: (Record<string, any> | null);
};

