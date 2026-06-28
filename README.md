# TalkToCity — Technical Documentation

> Cross-lingual travel assistant for Indian cities, powered by RAG + Google Gemini

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [RAG Pipeline](#3-rag-pipeline)
4. [AI Integration](#4-ai-integration)
5. [Backend](#5-backend)
6. [Frontend](#6-frontend)
7. [Data & Chunking](#7-data--chunking)
8. [Evaluation](#8-evaluation)
9. [Deployment](#9-deployment)
10. [UI Design](#10-ui-design)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Project Overview

TalkToCity is a bilingual travel assistant that answers questions about Indian cities in **English and Hindi**. It uses a **Retrieval-Augmented Generation (RAG)** pipeline to retrieve relevant passages from curated local knowledge and generate grounded answers via **Google Gemini**.

### Key Features

| Feature | Description |
|---|---|
| **Bilingual** | Questions and answers in English and Hindi |
| **Grounded answers** | Strictly based on curated local data — no hallucination |
| **City scoping** | Search filtered to Delhi, Mumbai, or Udaipur |
| **Source citations** | Every answer includes chunk IDs used as evidence |
| **Query expansion** | Automatic synonym enrichment for better retrieval recall |
| **Smart rechunking** | Merges small data chunks for richer context per retrieval |

### Cities Covered

| City | Chunks (original) | Chunks (rechunked) | Avg size |
|---|---|---|---|
| Delhi | 30 | 13 | 729 chars |
| Mumbai | 22 | 11 | 529 chars |
| Udaipur | 65 | 36 | 1056 chars |

---

## 2. System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         User's Browser                          │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP :5173
┌───────────────────────────▼─────────────────────────────────────┐
│                    Podman Pod / Docker Network                   │
│                                                                  │
│  ┌─────────────────────┐     ┌──────────────────────────────┐   │
│  │   React + Nginx     │     │      FastAPI (api.py)        │   │
│  │   talktocity-       │────▶│      talktocity-backend      │   │
│  │   frontend  :80     │     │      :8000                   │   │
│  └─────────────────────┘     └──────────┬───────────────────┘   │
│                                         │                        │
│                        ┌────────────────▼──────────────────┐    │
│                        │   PostgreSQL + pgvector            │    │
│                        │   talktocity-db  :5432             │    │
│                        └───────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
                            │ HTTPS
┌───────────────────────────▼─────────────────────────────────────┐
│                  Google Gemini API (cloud)                       │
│         generativelanguage.googleapis.com                        │
└─────────────────────────────────────────────────────────────────┘
```

### Container Breakdown

```
Host Ports           Container              Image
─────────────────────────────────────────────────────────
:5173 ──────────▶  talktocity-frontend     nginx:alpine (built)
:8000 ──────────▶  talktocity-backend      python:3.11-slim (built)
:5433 ──────────▶  talktocity-db           pgvector/pgvector:pg16
```

---

## 3. RAG Pipeline

### What is RAG?

RAG (Retrieval-Augmented Generation) combines a **vector search engine** with a **large language model**. Instead of relying on the LLM's training data, RAG retrieves relevant passages from a curated knowledge base and passes them as grounded context to the LLM.

```
Traditional LLM:
  User question ──▶ LLM (training data) ──▶ Answer (may hallucinate)

RAG:
  User question ──▶ Vector Search ──▶ Relevant chunks
                                            │
                                            ▼
                               LLM (only uses retrieved context) ──▶ Grounded Answer
```

### End-to-End Search Flow

```
┌──────────────────────────────────────────────────────────────────┐
│  1. USER INPUT                                                    │
│     question="best food in Udaipur"  city="Udaipur"  lang="en"  │
└──────────────────────────┬───────────────────────────────────────┘
                           │ POST /api/search
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  2. QUERY EXPANSION  (rag_core.py)                               │
│     "best food in Udaipur" + "food places street food           │
│      restaurants eat famous dishes"                              │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  3. EMBEDDING  (all-MiniLM-L6-v2)                                │
│     expanded query ──▶ 384-dimensional dense vector             │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  4. VECTOR SEARCH  (PGVector)                                    │
│     cosine similarity against stored embeddings                  │
│     filter: city = "Udaipur"   k = 4 chunks                    │
└──────────────────────────┬───────────────────────────────────────┘
                           │ top-4 chunks + metadata
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  5. CONTEXT BUILD  (build_context)                               │
│     [SOURCE 1] chunk_id: udaipur_eat_01_merged                  │
│     city: Udaipur  section: Eat                                 │
│     text: Udaipur has excellent street food...                  │
│     [SOURCE 2] ...                                               │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  6. LLM CALL  (Gemini API)                                       │
│     System: "You are a travel assistant. Use ONLY the context." │
│     Context: [retrieved chunks]                                  │
│     Question: "best food in Udaipur"                            │
└──────────────────────────┬───────────────────────────────────────┘
                           │ raw LLM output
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  7. PARSE RESPONSE  (split_answer_and_sources)                   │
│     answer: "Udaipur is famous for..."                          │
│     sources: ["udaipur_eat_01_merged", "udaipur_eat_02_merged"] │
└──────────────────────────┬───────────────────────────────────────┘
                           │ { answer, sources }
                           ▼
                    ┌──────────────┐
                    │   Browser    │
                    └──────────────┘
```

### Query Expansion

Expansion rules are loaded from `expansion_rules.json` at startup — no code changes needed to add new languages or topics.

```
expansion_rules.json
│
├── food: ["food","eat","खाना","भोजन"] → "food places street food restaurants"
├── hotel: ["hotel","stay","होटल"] → "stay hotels accommodation budget luxury"
├── shopping: ["market","बाज़ार"] → "shopping markets things to buy bazaars"
├── sightseeing: ["visit","itinerary","घूमना"] → "tourist attractions sightseeing"
├── transport: ["train","metro","ट्रेन"] → "transport getting around train bus"
├── history: ["fort","palace","किला"] → "history heritage fort palace temple"
├── budget: ["cheap","सस्ता"] → "budget cheap affordable free"
└── weather: ["season","मौसम"] → "weather climate best time to visit"
```

### Module Dependencies

```
rag_core.py          ← foundation — no internal imports
    │
    ├── pipeline_en.py     (English answers)
    ├── pipeline_hi.py     (Hindi answers)
    ├── ingest.py          (data loading)
    ├── search_debug.py    (debug retrieval)
    └── eval.py            (evaluation)
              │
           api.py          (imports pipeline_en + pipeline_hi + rag_core)
```

---

## 4. AI Integration

### Google Gemini

```
┌─────────────────────────────────────────────────────────────┐
│  call_llm(prompt, temperature, num_predict)                  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  call_gemini()                                        │   │
│  │                                                       │   │
│  │  POST https://generativelanguage.googleapis.com/     │   │
│  │       v1beta/models/{GEMINI_MODEL}:generateContent   │   │
│  │                                                       │   │
│  │  Headers:  X-goog-api-key: {GEMINI_API_KEY}          │   │
│  │  Body:     { contents, generationConfig }             │   │
│  │  Timeout:  60 seconds                                 │   │
│  │  MaxTokens: 2048                                      │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Model Options

| Model | Speed | Quality | Notes |
|---|---|---|---|
| `gemini-2.0-flash-lite` | Fastest | Good | Default. Free tier. |
| `gemini-2.0-flash` | Fast | Better | Free tier. |
| `gemini-2.5-flash` | Medium | Best | Recommended for Hindi. |
| `gemini-3-flash-preview` | Medium | Excellent | Latest. Best Hindi quality. |

### English vs Hindi Pipeline

```
English (pipeline_en.py)              Hindi (pipeline_hi.py)
─────────────────────────────         ─────────────────────────────────
k = 4 chunks retrieved                k = 5 chunks retrieved
temperature = 0.1                     temperature = 0.2
num_predict = 1024                    num_predict = 1024 (maxTokens 2048)

Prompt language: English              Prompt language: Hindi
Instruction: "Answer in English"      Instruction: "उत्तर हिंदी में दें"
Transliteration: none                 Transliteration: all English words
                                       converted to Hindi script

Format expected:                      Format expected:
  Answer:                               उत्तर:
  <detailed answer>                     <विस्तृत उत्तर>
  Sources:                              स्रोत:
  - chunk_id                            - chunk_id
```

### Answer Parsing

```
Raw LLM output:
  "उत्तर:\nउदयपुर में खाने के लिए...\n\nस्रोत:\n- udaipur_eat_01_merged"
                │
                ▼
  split_answer_and_sources()
                │
    ┌───────────┴───────────┐
    │                       │
    ▼                       ▼
  answer_text           sources[]
  "उदयपुर में..."       ["udaipur_eat_01_merged"]

Edge cases handled:
  ✓ Missing sources section → empty list returned
  ✓ Hindi marker स्रोत: → detected and split
  ✓ Placeholder <chunk_id> → filtered out
  ✓ Answer: / उत्तर: prefix → stripped
```

---

## 5. Backend

### File Structure

```
talktocity/
├── api.py                  ← FastAPI server (entry point)
├── rag_core.py             ← shared RAG utilities
├── pipeline_en.py          ← English RAG pipeline
├── pipeline_hi.py          ← Hindi RAG pipeline
├── ingest.py               ← data loading into PGVector
├── rechunk.py              ← merge small chunks into larger ones
├── eval.py                 ← retrieval quality evaluation
├── search_debug.py         ← debug vector search (no LLM)
├── expansion_rules.json    ← query expansion config (no code needed to edit)
├── Dockerfile
├── requirements.txt
└── data/
    ├── delhi_chunks.json
    ├── delhi_chunks_large.json   ← generated by rechunk.py
    ├── mumbai_chunks.json
    ├── mumbai_chunks_large.json
    ├── udaipur_chunks.json
    └── udaipur_chunks_large.json
```

### API Endpoints

```
GET  /health
     Response: { status, llm_backend, database }

POST /api/search
     Body:     { question: string, city: string|null, lang: "en"|"hi" }
     Response: { answer: string, sources: string[] }
     Errors:   400 empty question
               503 Gemini unreachable
               500 unexpected error
```

### api.py Request Flow

```
POST /api/search
      │
      ▼
  validate question (non-empty)
      │
      ▼
  k = 5 (Hindi) or 4 (English)
      │
      ▼
  retrieve_chunks(question, city, k)
      │
      ├── no docs? ──▶ return "Information not available"
      │
      ▼
  generate_grounded_hindi_answer()   ← if lang == "hi"
  generate_grounded_answer()         ← if lang == "en"
      │
      ▼
  split_answer_and_sources(raw)
      │
      ▼
  return SearchResponse(answer, sources)
```

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | ✅ | — | PostgreSQL connection string |
| `GEMINI_API_KEY` | ✅ | — | Google Gemini API key. Server refuses to start without it. |
| `GEMINI_MODEL` | ❌ | `gemini-2.0-flash-lite` | Gemini model name |
| `HF_HUB_DISABLE_IMPLICIT_TOKEN` | ❌ | — | Set to `1` to suppress HuggingFace warnings |

### Requirements

```
fastapi               # REST API framework
uvicorn[standard]     # ASGI server (Dockerfile CMD)
langchain-postgres    # PGVector vector store
langchain-huggingface # HuggingFaceEmbeddings class
sentence-transformers # Runtime dep — loads all-MiniLM-L6-v2
psycopg[binary]       # PostgreSQL driver (raw SQL in ingest.py)
python-dotenv         # .env file loading
requests              # HTTP client for Gemini API
```

---

## 6. Frontend

### Tech Stack

| Technology | Usage |
|---|---|
| React 18 | UI framework — functional components + hooks |
| Vite | Build tool. Dev server proxies `/api` → `localhost:8000` |
| CSS Modules | Scoped styling — no global CSS conflicts |
| Nginx | Serves production build. Proxies `/api` to backend. |

### File Structure

```
talktocity-react/
├── Dockerfile
├── nginx.conf              ← proxies /api to backend, SPA routing fallback
├── vite.config.js          ← dev proxy config
├── index.html
└── src/
    ├── main.jsx
    ├── App.jsx             ← view router (home / search / auth)
    ├── App.module.css      ← ambient blobs, page layout
    ├── styles/
    │   └── global.css      ← CSS variables, body, keyframes
    ├── api/
    │   └── search.js       ← fetch wrapper, 2-min AbortController timeout
    ├── hooks/
    │   └── useCarousel.js  ← carousel state, 4.5s auto-advance
    ├── components/
    │   ├── Topbar.jsx
    │   └── Topbar.module.css
    └── pages/
        ├── HomePage.jsx    ← city carousel
        ├── SearchPage.jsx  ← search + results
        └── AuthPage.jsx    ← login/signup (UI only)
```

### Component Hierarchy

```
App.jsx  (view router)
│
├── HomePage
│   ├── Topbar
│   ├── City slides (Udaipur / Delhi / Mumbai)
│   ├── Hero copy + CTA button
│   ├── Mini city cards
│   ├── Arrow controls
│   └── Dot navigation
│
├── SearchPage
│   ├── Topbar
│   ├── Language toggle (EN | हि)
│   ├── Search bar (input + city select + button)
│   ├── Trending chips  ← trigger search on click
│   ├── Skeleton loader (while loading)
│   ├── Error display
│   └── Result box (answer + source chips)
│
└── AuthPage
    ├── Topbar
    ├── Tab toggle (Login | Sign Up)
    └── Form (email + password + optional name)
```

### Search Page State Flow

```
initial state:
  question=""  city="Udaipur"  lang="en"
  loading=false  result=null  error=null

User types question → setQuestion()

User clicks Search / presses Enter
  ↓
  runSearch(question, city, lang)
    setLoading(true), setError(null), setResult(null)
    ↓
    searchCity({ question, city, lang })  [2-min timeout]
      POST /api/search
      ↓
    success → setResult(data)
    error   → setError(err.message)
    timeout → setError("Request timed out...")
    finally → setLoading(false)

User clicks chip
  ↓
  handleChip(chip)
    setQuestion(chip.question)
    setCity(chip.city)
    runSearch(chip.question, chip.city, lang)  ← values passed directly
    (not reading state — state update is async)

User switches language
  ↓
  handleLang(newLang)
    setLang(newLang)
    setResult(null)    ← clear stale result
    setError(null)
```

---

## 7. Data & Chunking

### Data Source

All travel content is sourced from **Wikivoyage** (CC-BY-SA licence). Content covers 12 categories per city:

```
understand  get_in   get_around  see    do
eat         drink    sleep       buy    stay_safe
stay_healthy         go_next
```

### Chunk Schema

```json
{
  "doc_id":      "wikivoyage_udaipur",
  "chunk_id":    "udaipur_eat_01_merged",
  "city":        "Udaipur",
  "country":     "India",
  "source":      "Wikivoyage",
  "source_url":  "https://en.wikivoyage.org/wiki/Udaipur",
  "section":     "Eat",
  "subsection":  "Street Food",
  "tags":        ["food", "street-food", "local-cuisine"],
  "text":        "Udaipur has excellent street food..."
}
```

### Rechunking Strategy

```
Original chunks (avg 315 chars — too small for quality RAG):
  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
  │ chunk_01     │ │ chunk_02     │ │ chunk_03     │
  │ section: Eat │ │ section: Eat │ │ section: See │
  │ 180 chars    │ │ 210 chars    │ │ 250 chars    │
  └──────────────┘ └──────────────┘ └──────────────┘

rechunk.py rules:
  ✓ Same city + same section → merge
  ✓ Stop when combined > 1000 chars (max 1500)
  ✗ Never merge across sections

After rechunking (avg 729-1056 chars):
  ┌────────────────────────────┐ ┌──────────────┐
  │ chunk_01_merged            │ │ chunk_03     │
  │ section: Eat               │ │ section: See │
  │ 390 chars (01+02 merged)   │ │ 250 chars    │
  └────────────────────────────┘ └──────────────┘
```

### Ingest Logic

```
ingest.py  per-city decision tree:

  For each city in incoming chunk files:
    │
    ├── City NOT in DB yet
    │   └──▶ INSERT all chunks
    │
    ├── City in DB, chunk_ids IDENTICAL
    │   └──▶ SKIP (already up to date)
    │
    └── City in DB, chunk_ids DIFFERENT
        (e.g. switched from small → large chunks)
        ├──▶ DELETE all old chunks for that city
        └──▶ INSERT new chunks

This prevents orphaned small chunks coexisting with large chunks in the DB.
```

### File Priority

```
data/
├── delhi_chunks.json          ← source (original)
├── delhi_chunks_large.json    ← preferred (rechunked)
├── mumbai_chunks.json
├── mumbai_chunks_large.json
└── ...

ingest.py selects per city:
  if *_chunks_large.json exists → use it
  else                          → use *_chunks.json
  NEVER loads both for the same city
```

---

## 8. Evaluation

### Test Dataset

`queries.json` — 200 bilingual queries with expected retrieval targets.

```json
{
  "id": "DEL_EAT_001",
  "city": "Delhi",
  "category": "eat",
  "query_en": "Best street food in Delhi",
  "query_hi": "दिल्ली में सबसे अच्छा स्ट्रीट फूड",
  "expected_sections": ["Eat"],
  "expected_keywords": ["Chandni Chowk", "parathas", "chaat"]
}
```

| Property | Value |
|---|---|
| Total queries | 200 |
| Delhi | 100 queries |
| Mumbai | 50 queries |
| Udaipur | 50 queries |
| Categories | 12 (eat, see, sleep, buy, etc.) |
| Keywords per query | 3 |

### Metrics

```
Keyword hit rate:  % of expected_keywords found in top-k retrieved text
                   Primary quality signal for retrieval

Section hit rate:  % of expected_sections matched in chunk metadata
                   Measures topical precision

Perfect retrieval: % of queries where all 3 keywords found
                   Strictest measure — indicates complete coverage

Per-city score:    Keyword hit rate broken down by Delhi / Mumbai / Udaipur
                   Shows which city data is weakest

Per-category score: Keyword hit rate by category (eat, see, sleep...)
                    Reveals specific data gaps to fix
```

### Running Evaluation

```bash
# Full evaluation — all 200 queries, English
podman exec talktocity-backend python eval.py

# Hindi queries
podman exec talktocity-backend python eval.py --lang hi

# Quick test — first 20 queries only
podman exec talktocity-backend python eval.py --limit 20

# Drill into weak areas
podman exec talktocity-backend python eval.py --city Delhi --category eat

# Verbose — see every query result
podman exec talktocity-backend python eval.py --verbose

# Save full results
podman exec talktocity-backend python eval.py --output results.json
```

### Sample Output

```
Evaluating 200 queries  |  lang=en  |  k=5

========================================
EVALUATION SUMMARY
========================================
Queries evaluated : 200
Keyword hit rate  : 61.2%
Section hit rate  : 78.4%
Perfect retrieval : 89/200 (45%)

By city:
  Udaipur    74.1%  ██████████████
  Delhi      58.3%  ████████████
  Mumbai     51.7%  ██████████

By category (best → worst):
  eat            81.2%  ████████████████
  see            72.4%  ██████████████
  sleep          65.0%  █████████████

Worst 5 queries:
  [DEL_BUY_003] score=0%  misses=['handicrafts', 'Dilli Haat', 'souvenirs']
```

---

## 9. Deployment

### Container Architecture

```
Podman Pod (Windows) / Docker Bridge Network (Linux)
────────────────────────────────────────────────────────────────────

  talktocity-frontend          talktocity-backend
  ┌──────────────────┐         ┌──────────────────────────────────┐
  │ nginx:alpine     │         │ python:3.11-slim                 │
  │ :80 (→ :5173)   │──/api/──▶ :8000                           │
  │                  │         │                                  │
  │ Serves React SPA │         │ FastAPI + RAG pipeline           │
  │ Proxies /api     │         │ HuggingFace embeddings (local)   │
  └──────────────────┘         └──────────────┬───────────────────┘
                                              │
                               ┌──────────────▼───────────────────┐
                               │ pgvector/pgvector:pg16            │
                               │ :5432 (→ :5433)                  │
                               │                                  │
                               │ PostgreSQL + pgvector extension  │
                               │ Persistent named volume          │
                               └───────────────────────────────────┘

                               External:
                               ┌───────────────────────────────────┐
                               │ Google Gemini API                 │
                               │ generativelanguage.googleapis.com │
                               └───────────────────────────────────┘
```

### Run Scripts

| Script | Platform | Runtime |
|---|---|---|
| `run.sh` | Windows (Git Bash) | Podman Desktop |
| `run.ps1` | Windows (PowerShell) | Podman Desktop |
| `run-linux.sh` | Linux / Mac | Docker or Podman (auto-detected) |
| `run.cmd` | Windows (CMD) | Docker Desktop |

### Commands

```bash
./run.sh                  # start all containers
./run.sh setup            # first-time: rechunk → start stack → ingest
./run.sh rechunk          # data changed: rechunk → rebuild backend → ingest
./run.sh ingest           # re-run ingest only (skips existing chunks)
./run.sh stop             # stop and remove all containers
./run.sh logs             # tail logs from all 3 containers
```

### First-Time Deployment

```bash
# 1. Set Gemini API key and start the stack
GEMINI_API_KEY=your-key ./run.sh

# 2. First-time setup (rechunk + ingest)
./run.sh setup

# 3. Verify
curl http://localhost:8000/health

# 4. Open UI
http://localhost:5173
```

### Rechunk + Ingest Flow

```
./run.sh rechunk
      │
      ├── 1. python rechunk.py (on host)
      │        reads: data/*_chunks.json
      │        writes: data/*_chunks_large.json
      │
      ├── 2. podman stop talktocity-backend
      │
      ├── 3. podman rmi talktocity-backend --force
      │
      ├── 4. podman build talktocity/
      │        COPY . . bakes *_chunks_large.json into image
      │
      ├── 5. podman run talktocity-backend
      │
      └── 6. python ingest.py
               detects chunk_ids changed → deletes old → inserts new
```

### Adding a New City

```
1. Add new_city_chunks.json to talktocity/data/
   (follow existing schema: chunk_id, city, section, subsection, tags, text)

2. Rechunk, rebuild, ingest:
   ./run.sh rechunk

3. Add city to frontend:
   SearchPage.jsx  → CITIES array
   useCarousel.js  → CITIES array
   HomePage.jsx    → new slide with data-city attribute and CSS image var

4. Add CSS variable for city image:
   global.css → --newcity-img: url('...')
   HomePage.module.css → .slide[data-city="NewCity"]::before

5. Add expansion rules if needed:
   expansion_rules.json → new rule with city-specific terms
```

### Networking

```
Connection                    How
──────────────────────────────────────────────────────────────
Browser → Frontend            http://localhost:5173
Frontend → Backend (prod)     nginx proxy_pass to localhost:8000 (same pod)
Frontend → Backend (dev)      Vite proxy to http://localhost:8000
Backend → Database (Podman)   postgresql://localhost:5432 (shared pod network)
Backend → Database (Docker)   postgresql://talktocity-db:5432 (bridge network)
Backend → Gemini              HTTPS to generativelanguage.googleapis.com
Host → Backend (direct)       http://localhost:8000
```

---

## 10. UI Design

### Design Language

Dark luxury aesthetic — deep navy backgrounds, glass-morphism panels, cyan accent lighting.

```css
--bg:       #0a0f18   /* page background */
--accent:   #79e7ff   /* primary accent — buttons, underlines, chips */
--accent-2: #ffc7e6   /* secondary — ambient light blobs */
--panel:    rgba(255,255,255,0.08)   /* glass panel background */
--shadow:   0 20px 70px rgba(0,0,0,0.38)

Fonts:
  Playfair Display 600-800   → city taglines (serif, editorial)
  Inter 400-800              → all UI text (clean, versatile)
```

### Pages

```
Home Page
├── Full-bleed city photography (Unsplash)
├── Ken Burns zoom animation on active slide
├── City name in large uppercase (clamp 4rem–7.4rem)
├── Tagline in Playfair Display
├── "Explore this city" ghost button → Search page
├── Slide counter (01 / 03)
├── Prev/Next arrow controls + dot navigation
└── Mini city cards at bottom (hover lifts + cyan border)

Search Page
├── Frosted glass panel (backdrop-filter: blur)
├── EN | हि language toggle
├── Search input + city dropdown + Search button
├── Trending chips (click = instant search)
├── Shimmer skeleton while loading
├── Answer text (white-space: pre-wrap)
└── Source chips (cyan border, chunk_id labels)

Auth Page
├── Login / Sign Up tab toggle
├── Name field (Sign Up only)
├── Email + Password fields
└── Submit button (gradient cyan→white)
```

### Responsive Breakpoints

```
> 1100px   Full layout — 2-column hero grid, vertical quick-stat
< 1100px   Hero collapses to 1 column, quick-stat goes horizontal
< 860px    Compact — mini panels stack vertically,
           search bar stacks vertically, arrows reposition to bottom
```

---

## 11. Troubleshooting

### Common Issues

| Issue | Cause | Fix |
|---|---|---|
| Backend 500 on search | Gemini error or DB issue | `./run.sh logs` — check backend logs |
| Gemini 400 Bad Request | Wrong model name | Check `GEMINI_MODEL` env var. Valid: `gemini-2.0-flash-lite`, `gemini-3-flash-preview` |
| Frontend 502 Bad Gateway | Backend not running | `./run.sh logs` then `podman ps` |
| pip BrokenPipeError in build | Network drop inside Podman VM | Re-run `./run.sh` — split-layer Dockerfile resumes from cache |
| `log: command not found` in run.sh | `log()` not defined (Linux function vs Windows) | Use updated `run.sh` |
| `python3: command not found` | Windows has `python` not `python3` | Updated script auto-detects |
| Podman machine already running | Podman Desktop auto-manages VM | Fixed — script uses `machine inspect --format {{.State}}` |
| HuggingFace warning on startup | Unauthenticated API access | Set `HF_HUB_DISABLE_IMPLICIT_TOKEN=1` (already in run scripts) |
| `position_ids UNEXPECTED` warning | Benign library version mismatch | Suppressed in `rag_core.py` via `warnings.filterwarnings` |
| Old small chunks + new large chunks in DB | Rechunked but didn't clean DB | `./run.sh rechunk` handles this — detects ID change, deletes old, inserts new |
| UI hangs on search | Gemini taking >2 min | `AbortController` timeout fires after 2 min with clear error message |

### Useful Debug Commands

```bash
# Check what's running
podman ps

# View logs
./run.sh logs
podman logs talktocity-backend
podman logs talktocity-frontend

# Test backend directly
curl http://localhost:8000/health
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"question":"best food in Delhi","city":"Delhi","lang":"en"}'

# Check what's in the DB
podman exec talktocity-db psql -U postgres -d talktocity \
  -c "SELECT cmetadata->>'city', COUNT(*) FROM langchain_pg_embedding GROUP BY 1;"

# Debug retrieval without LLM
podman exec -it talktocity-backend python search_debug.py

# Run evaluation
podman exec talktocity-backend python eval.py --limit 20

# Force clean rebuild
./run.sh stop
podman rmi talktocity-backend --force
podman rmi talktocity-frontend --force
GEMINI_API_KEY=your-key ./run.sh
```

---

*© 2026 TalkToCity — Built with FastAPI, React, PGVector, and Google Gemini*
