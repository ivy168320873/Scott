// 車銷 CRM Service Worker
//
// 策略：靜態資源 cache-first、其餘一律 network-only。
// 客戶資料與待辦必須即時，快取舊資料會讓業務打錯電話或漏掉跟進，
// 因此刻意不快取 HTML 與表單回應。
const CACHE = 'car-crm-v1';
const STATIC_ASSETS = ['/manifest.json', '/static/icon.svg'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(STATIC_ASSETS)).catch(() => {
      // 離線安裝失敗不應阻擋 SW 啟用
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // 僅快取同源的靜態資源
  if (url.origin === self.location.origin &&
      (url.pathname.startsWith('/static/') || url.pathname === '/manifest.json')) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then((response) => {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
          return response;
        });
      })
    );
  }
  // 其他請求不攔截 → 交給瀏覽器直接走網路，確保資料是最新的
});
