# Stage 1: Build Vite React App
FROM node:22-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
# Шаблон конфігурації: entrypoint nginx підставляє KAIRO_* під час старту
COPY default.conf.template /etc/nginx/templates/default.conf.template
ENV KAIRO_RESOLVER=127.0.0.11     KAIRO_SVC_SUFFIX=""     NGINX_ENVSUBST_FILTER=KAIRO_

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
