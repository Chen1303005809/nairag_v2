FROM node:22.14.0-alpine3.21 AS build

WORKDIR /app

ARG VITE_LLM_MAX_CONVERSATION_MESSAGES=200
ARG VITE_LLM_MAX_CONVERSATION_CHARS=30000
ENV VITE_LLM_MAX_CONVERSATION_MESSAGES=${VITE_LLM_MAX_CONVERSATION_MESSAGES} \
    VITE_LLM_MAX_CONVERSATION_CHARS=${VITE_LLM_MAX_CONVERSATION_CHARS}

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
