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
RUN pnpm install --frozen-lockfile --ignore-scripts && \
    pnpm rebuild esbuild

# 复制源码并构建
COPY apps/web/ ./
RUN npx vite build

# ---- Stage 2: Serve ----
FROM docker.m.daocloud.io/nginx:alpine

# 移除 nginx 默认配置，避免冲突
RUN rm -f /etc/nginx/conf.d/default.conf

# 复制两份 nginx 配置到 templates 目录（不在 conf.d 下，避免被自动加载）
# - nginx-tls.conf: 生产环境（80 重定向 + 443 TLS）
# - nginx-http.conf: 开发环境（仅 80，无 TLS）
COPY deployments/compose/nginx.conf /etc/nginx/templates/nginx-tls.conf
COPY deployments/compose/nginx-http.conf /etc/nginx/templates/nginx-http.conf

# 修改主 nginx.conf，从 /tmp（tmpfs，运行时可写）加载配置
# web 容器以 read_only 模式运行，/etc/nginx/conf.d 不可写，/tmp 是 tmpfs
RUN sed -i 's|include /etc/nginx/conf.d/\*.conf;|include /tmp/*.conf;|' /etc/nginx/nginx.conf

# 复制构建产物
COPY --from=builder /build/dist /usr/share/nginx/html

# 通过 NGINX_CONF 环境变量选择配置文件
# 开发环境设为 nginx-http.conf，生产环境设为 nginx-tls.conf
ENV NGINX_CONF=nginx-http.conf

EXPOSE 80 443

# 运行时将选中的配置复制到 /tmp/default.conf（tmpfs 可写），然后启动 nginx
CMD ["sh", "-c", "cp /etc/nginx/templates/${NGINX_CONF} /tmp/default.conf && nginx -g 'daemon off;'"]
