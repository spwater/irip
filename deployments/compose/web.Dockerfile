# IRIP Web Dockerfile（多阶段构建）
# Stage 1: node:22-slim 构建 React 前端
# Stage 2: nginx:alpine 提供静态服务 + /api 反代

# ---- Stage 1: Build ----
FROM docker.m.daocloud.io/node:22-slim AS builder

WORKDIR /build

# 配置国内 npm 镜像 + 启用 corepack
# COREPACK_NPM_REGISTRY 让 corepack 从国内镜像下载 pnpm 二进制（否则走 registry.npmjs.org 会超时）
ENV COREPACK_NPM_REGISTRY=https://registry.npmmirror.com
RUN npm config set registry https://registry.npmmirror.com && \
    corepack enable pnpm

# 先复制依赖清单，利用 Docker 层缓存
COPY apps/web/package.json apps/web/pnpm-lock.yaml apps/web/.npmrc ./
# pnpm 11.15.1 严格模式会阻止 esbuild 构建脚本，用 --ignore-scripts 跳过再单独 rebuild
# BuildKit 缓存挂载：pnpm store 跨构建持久化，npm 包不用每次重新下载
RUN --mount=type=cache,target=/build/.pnpm-store \
    pnpm config set store-dir /build/.pnpm-store && \
    pnpm install --frozen-lockfile --ignore-scripts && \
    pnpm rebuild esbuild

# 复制源码并构建
COPY apps/web/ ./
# BuildKit 缓存挂载：Vite 构建缓存跨构建持久化，TS 增量编译更快
RUN --mount=type=cache,target=/build/node_modules/.vite \
    npx tsc --noEmit && npx vite build

# ---- Stage 2: Serve ----
FROM docker.m.daocloud.io/nginx:alpine

# 复制 nginx 配置
COPY deployments/compose/nginx.conf /etc/nginx/conf.d/default.conf

# 复制构建产物
COPY --from=builder /build/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
