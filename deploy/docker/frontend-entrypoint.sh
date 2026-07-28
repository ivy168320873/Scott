#!/bin/sh
# 在 nginx 啟動前產生 /env.js，把後端位址注入前端。
#
# 這讓同一個前端映像可以部署到不同環境，而不需要重新建置。

set -e

API_BASE_URL="${STUDIO_API_BASE_URL:-http://localhost:8000}"

cat > /usr/share/nginx/html/env.js <<EOF
window.__SCOTT_STUDIO_ENV__ = {
  API_BASE_URL: "${API_BASE_URL}"
};
EOF

echo "[scott-studio] injected API_BASE_URL=${API_BASE_URL}"
