# MetaForge Dashboard — multi-stage build
# Dev:  served by Vite with HMR + API proxy
# Prod: static assets served by nginx

# ── Stage 1: build ──────────────────────────────────────────────────
FROM node:20-alpine AS build

WORKDIR /app

COPY package.json ./
RUN npm install

COPY . .
RUN npx vite build

# ── Stage 2: serve ──────────────────────────────────────────────────
FROM nginx:alpine AS production

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
