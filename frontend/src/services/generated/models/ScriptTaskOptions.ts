/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 腳本類任務的共用選項。
 */
export type ScriptTaskOptions = {
    /**
     * 指定文字模型；留空使用預設
     */
    model_id?: (string | null);
    /**
     * 重新拆解時是否清除既有分鏡
     */
    replace_existing?: boolean;
    /**
     * 失敗時的重試次數
     */
    max_retries?: number;
};

