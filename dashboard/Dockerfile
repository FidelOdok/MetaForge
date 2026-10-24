# MetaForge Dashboard — multi-stage build
# Dev:  served by Vite with HMR + API proxy
# Prod: static assets served by nginx

# ── Stage 1: build ──────────────────────────────────────────────────
FROM node:20-alpine AS build

WORKDIR /app

# Install from the lockfile. With only package.json, every build resolved the
# whole tree afresh from the registry, so an upstream release could break an
# image nobody changed -- it did from 2026-10-22 (npm: "Cannot read properties
# of null (reading edgesOut)"). npm ci installs exactly what was tested.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# This image's nginx.conf proxies /faro and /otlp to the collectors, so RUM has
# somewhere to ship to. It is off by default everywhere else (see src/main.tsx).
ENV VITE_RUM_ENABLED=true
RUN npx vite build

# ── Stage 2: serve ──────────────────────────────────────────────────
FROM nginx:alpine AS production

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
