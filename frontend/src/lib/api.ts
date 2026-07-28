/**
 * Studio API client 設定。
 *
 * 全部呼叫都走 OpenAPI 產生的 client（`src/services/generated`），
 * 不手寫 fetch —— 後端 API 是唯一規格來源。
 */
import { OpenAPI } from '../services/generated';

declare global {
  interface Window {
    __SCOTT_STUDIO_ENV__?: { API_BASE_URL?: string };
  }
}

/** 後端位址由執行期注入的 /env.js 提供；同源部署時留空即可。 */
OpenAPI.BASE = window.__SCOTT_STUDIO_ENV__?.API_BASE_URL ?? '';

/** 帶上 cookie，讓 Studio 沿用股票系統的登入 session。 */
OpenAPI.WITH_CREDENTIALS = true;
OpenAPI.CREDENTIALS = 'include';

/** 統一回應信封。 */
export interface Envelope<T> {
  success: boolean;
  data?: T | null;
  error?: { code: string; message: string; details?: Record<string, unknown> | null } | null;
}

/**
 * 從信封取出資料；失敗時拋出可讀錯誤。
 *
 * 後端所有端點（含 401/404/409/422）都使用同一信封，
 * 因此前端只需要這一個 helper。
 */
export function unwrap<T>(response: unknown): T {
  const envelope = response as Envelope<T>;
  if (!envelope?.success) {
    throw new Error(envelope?.error?.message || '請求失敗');
  }
  return envelope.data as T;
}

/** 把任意錯誤轉成可顯示的訊息。 */
export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  const body = (error as { body?: Envelope<unknown> })?.body;
  if (body?.error?.message) return body.error.message;
  const status = (error as { status?: number })?.status;
  if (status === 401) return '尚未登入，請先登入股票系統首頁';
  return '發生未預期的錯誤';
}
