# TalkToCity — React Frontend

Migrated from `frontend/index.html` to a Vite + React project.

## Project structure

```
talktocity-react/
├── index.html
├── vite.config.js          # dev proxy: /api → localhost:8000
├── package.json
├── backend/
│   └── server.py           # FastAPI server (POST /api/search)
└── src/
    ├── main.jsx
    ├── App.jsx              # view router (home / search / auth)
    ├── App.module.css
    ├── styles/
    │   └── global.css       # CSS variables, body, keyframes
    ├── api/
    │   └── search.js        # fetch wrapper → POST /api/search
    ├── hooks/
    │   └── useCarousel.js   # carousel state + auto-advance
    ├── components/
    │   ├── Topbar.jsx
    │   └── Topbar.module.css
    └── pages/
        ├── HomePage.jsx     + HomePage.module.css
        ├── SearchPage.jsx   + SearchPage.module.css   ← wired to backend
        └── AuthPage.jsx     + AuthPage.module.css
```

## 1. Frontend

```bash
cd talktocity-react
npm install
npm run dev          # http://localhost:5173
```

## 2. Backend (FastAPI)

Place `talktocity-react/` next to your existing `talktocity/` project folder,
then run from the **talktocity** root (where answer.py / answer1.py live):

```bash
pip install fastapi uvicorn
uvicorn talktocity-react.backend.server:app --reload --port 8000
```

Or copy `backend/server.py` into the `talktocity/` root and run:

```bash
uvicorn server:app --reload --port 8000
```

The Vite dev server proxies `/api/*` → `http://localhost:8000` automatically,
so no CORS issues during development.

## 3. Full stack (Podman)

Add this service to your pod alongside the existing DB container:

```bash
# Build the app image from the talktocity/ root
podman build -t talktocity-app .

# Frontend (Vite dev)
podman run -d --pod talktocity \
  -v "$(pwd)/talktocity-react:/frontend" \
  -w /frontend node:20-slim \
  sh -c "npm install && npm run dev -- --host"

# Backend (FastAPI)
podman run -d --pod talktocity \
  -v "$(pwd):/app" \
  -w /app talktocity-app \
  uvicorn backend.server:app --host 0.0.0.0 --port 8000
```

## API contract

**POST /api/search**

Request:
```json
{ "question": "Best food in Udaipur", "city": "Udaipur", "lang": "en" }
```

Response:
```json
{ "answer": "...", "sources": ["udaipur_food_01", "udaipur_food_02"] }
```

`lang` accepts `"en"` (default, uses answer1.py) or `"hi"` (uses answer.py).
