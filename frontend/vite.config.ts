import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Studio 前端一律掛在 /studio 之下，不佔用根路徑 —— 根路徑屬於既有股票系統。
export default defineConfig({
  base: '/studio/',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 開發時把 API 轉給後端，避免跨網域設定
      '/api/v1/studio': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
});
