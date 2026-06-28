# talktocity-react/Dockerfile

# ── Stage 1: build the React app ──────────────────────────────
FROM node:20-slim AS builder

WORKDIR /app
COPY package.json ./
RUN npm install

COPY . .

# Tell Vite where the backend API lives at runtime
# Override with --build-arg if needed
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL

RUN npm run build

# ── Stage 2: serve with Nginx ─────────────────────────────────
FROM nginx:alpine

# Copy built assets
COPY --from=builder /app/dist /usr/share/nginx/html

# Nginx config: serve React SPA + proxy /api to backend
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
