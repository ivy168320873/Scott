# Scott Studio 前端映像：Vite 建置後由 nginx 靜態服務。
#
# 後端位址在「執行期」而非建置期注入：容器啟動時產生 /env.js，
# 前端從 window.__SCOTT_STUDIO_ENV__ 讀取，因此同一個映像可部署到不同環境。

FROM node:22-alpine AS build

WORKDIR /app

RUN corepack enable

COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN pnpm install --no-frozen-lockfile

COPY frontend/ ./
RUN pnpm build


FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/docker/frontend-entrypoint.sh /docker-entrypoint.d/40-scott-studio-env.sh

RUN chmod +x /docker-entrypoint.d/40-scott-studio-env.sh

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -q --spider http://127.0.0.1/ || exit 1
