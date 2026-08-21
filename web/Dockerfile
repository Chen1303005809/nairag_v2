FROM node:22.14.0-alpine3.21 AS build

WORKDIR /app

ARG VITE_LLM_MAX_CONVERSATION_MESSAGES=200
ARG VITE_LLM_MAX_CONVERSATION_CHARS=30000
ARG VITE_API_BASE_URL=/api/v1
ARG VITE_CSRF_COOKIE_NAME=nairag_csrf
ARG VITE_PRE_AUTH_CSRF_COOKIE_NAME=nairag_pre_auth_csrf
ENV VITE_LLM_MAX_CONVERSATION_MESSAGES=${VITE_LLM_MAX_CONVERSATION_MESSAGES} \
    VITE_LLM_MAX_CONVERSATION_CHARS=${VITE_LLM_MAX_CONVERSATION_CHARS} \
    VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_CSRF_COOKIE_NAME=${VITE_CSRF_COOKIE_NAME} \
    VITE_PRE_AUTH_CSRF_COOKIE_NAME=${VITE_PRE_AUTH_CSRF_COOKIE_NAME}

COPY package.json package-lock.json ./
RUN npm ci

COPY . ./
RUN npm run build

FROM nginx:1.27.5-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD wget --quiet --tries=1 --spider http://127.0.0.1/healthz || exit 1
