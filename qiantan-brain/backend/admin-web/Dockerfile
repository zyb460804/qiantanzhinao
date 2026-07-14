FROM node:22-alpine AS build
WORKDIR /app
COPY backend/admin-web/package*.json ./
RUN npm ci
COPY backend/admin-web/ ./
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY deploy/nginx/nginx.conf /etc/nginx/nginx.conf
COPY deploy/nginx/conf.d/ /etc/nginx/conf.d/
COPY --from=build /app/dist/ /usr/share/nginx/html/admin/
EXPOSE 80 443
