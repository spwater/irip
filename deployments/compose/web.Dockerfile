# IRIP Web Dockerfile（多阶段构建）
# Stage 1: node:22-slim 构建 React 前端
# Stage 2: nginx:alpine 提供静态服务 + /api 反代

# ---- Stage 1: Build ----
FROM docker.m.daocloud.io/node:22-slim AS builder

WORKDIR /build

# 配置国内 npm 镜像 + 启用 corepack
RUN npm config set registry https://registry.npmmirror.com && \
    corepack enable pnpm

# 先复制依赖清单，利用 Docker 层缓存
COPY apps/web/package.json apps/web/pnpm-lock.yaml apps/web/.npmrc ./
# pnpm 11 的 ERR_PNPM_IGNORED_BUILDS 不影响包安装，仅退出码为 1
# 用 || true 容忍退出码，再手动 rebuild esbuild 使其 native binary 可用
RUN pnpm install --no-frozen-lockfile || true && \
    pnpm rebuild esbuild

# 复制源码并构建
COPY apps/web/ ./
RUN npx tsc --noEmit && npx vite build

# ---- Stage 2: Serve ----
FROM docker.m.daocloud.io/nginx:alpine

# 复制 nginx 配置
COPY deployments/compose/nginx.conf /etc/nginx/conf.d/default.conf

# 复制构建产物
COPY --from=builder /build/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
