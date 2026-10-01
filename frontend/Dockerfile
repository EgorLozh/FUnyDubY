# SPA собирается в builder-стадии, отдаётся nginx. Node нужен только здесь:
# в рантайм-образ попадает лишь статика, поэтому он остаётся маленьким.

FROM node:22-alpine AS build
WORKDIR /app

# Сначала зависимости: слой кешируется, пока не менялся package.json
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src

ARG VITE_API_BASE=/api
ENV VITE_API_BASE=$VITE_API_BASE
ARG VITE_BASE_PATH=/
ENV VITE_BASE_PATH=$VITE_BASE_PATH
RUN npm run build


FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
