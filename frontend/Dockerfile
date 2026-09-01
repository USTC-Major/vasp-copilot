# VASP-Doctor 前端镜像：Node 构建产物 -> nginx 托管（SPA fallback + API 反代）
# 反代路由与本地开发 vite.config.ts 代理保持一致：
#   /api/v1 -> backend:8000（工具箱主后端）
#   /ai/v1  -> ai_mode:8500（智能模式后端）
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
CMD ["nginx", "-g", "daemon off;"]
