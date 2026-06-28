"""
api.py
------
FastAPI backend for TalkToCity.
Includes Google OAuth2 login and JWT session management.

Run locally:
    uvicorn api:app --reload --port 8000

Environment variables required:
    DATABASE_URL      — PostgreSQL connection string
    GEMINI_API_KEY    — Google Gemini API key
    GEMINI_MODEL      — Gemini model name (default: gemini-2.0-flash)
    GOOGLE_CLIENT_ID  — Google OAuth2 client ID
    JWT_SECRET        — Secret for signing session JWTs
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from jose import JWTError, jwt
import psycopg

import math
import re
from rag_core    import embeddings, retrieve_chunks, DATABASE_URL
from pipeline_hi import generate_grounded_hindi_answer
from pipeline_en import generate_grounded_answer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
JWT_SECRET       = os.getenv("JWT_SECRET", "change-this-in-production")
JWT_ALGORITHM    = "HS256"
JWT_EXPIRE_HOURS = 24 * 7   

RAW_DB_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")

# ── App ────────────────────────────────────────────────────────────────────

app = FastAPI(title="TalkToCity API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

bearer_scheme = HTTPBearer(auto_error=False)

# ── DB: users table ────────────────────────────────────────────────────────

def ensure_users_table():
    try:
        with psycopg.connect(RAW_DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS talktocity_users (
                        id          SERIAL PRIMARY KEY,
                        google_id   TEXT UNIQUE NOT NULL,
                        email       TEXT UNIQUE NOT NULL,
                        name        TEXT,
                        picture     TEXT,
                        created_at  TIMESTAMPTZ DEFAULT NOW(),
                        last_login  TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                # Search history table
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS talktocity_search_history (
                        id               SERIAL PRIMARY KEY,
                        user_google_id   TEXT NOT NULL REFERENCES talktocity_users(google_id) ON DELETE CASCADE,
                        question         TEXT NOT NULL,
                        city             TEXT,
                        lang             TEXT DEFAULT 'en',
                        answer           TEXT,
                        sources          TEXT[],
                        retrieval_score  FLOAT,
                        chunk_count      INT,
                        searched_at      TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                # Index for fast per-user lookups
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_history_user
                    ON talktocity_search_history(user_google_id, searched_at DESC)
                """)
            conn.commit()
        logger.info("Users + history tables ready.")
    except Exception as e:
        logger.error("Failed to create tables: %s", e)

def upsert_user(google_id: str, email: str, name: str, picture: str) -> dict:
    with psycopg.connect(RAW_DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO talktocity_users (google_id, email, name, picture, last_login)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (google_id) DO UPDATE SET
                    name       = EXCLUDED.name,
                    picture    = EXCLUDED.picture,
                    last_login = NOW()
                RETURNING id, google_id, email, name, picture
            """, (google_id, email, name, picture))
            row = cur.fetchone()
        conn.commit()
    return {"id": row[0], "google_id": row[1], "email": row[2], "name": row[3], "picture": row[4]}


@app.on_event("startup")
async def startup():
    import asyncio
    # Wait for DB to be fully ready — especially important when
    # the embedding model takes time to load (e.g. LaBSE)
    await asyncio.sleep(2)
    ensure_users_table()


# ── JWT ────────────────────────────────────────────────────────────────────

def create_session_token(user: dict) -> str:
    payload = {
        "sub":     user["google_id"],
        "email":   user["email"],
        "name":    user["name"],
        "picture": user["picture"],
        "exp":     datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_session_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)):
    if not credentials:
        return None  # This allows the request to proceed as a 'Guest'
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        # If a token IS provided but it is invalid, we still throw an error
        raise HTTPException(status_code=401, detail="Invalid or expired session token.")


# ── History helpers ────────────────────────────────────────────────────────

HISTORY_LIMIT = 20

def save_search_history(
    google_id: str, question: str, city: str | None, lang: str,
    answer: str, sources: list[str], retrieval_score: float, chunk_count: int
) -> None:
    """Save a search to history, keeping only the last HISTORY_LIMIT per user."""
    try:
        with psycopg.connect(RAW_DB_URL) as conn:
            with conn.cursor() as cur:
                # Insert new entry
                cur.execute("""
                    INSERT INTO talktocity_search_history
                        (user_google_id, question, city, lang, answer, sources, retrieval_score, chunk_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (google_id, question, city, lang, answer, sources, retrieval_score, chunk_count))

                # Delete oldest entries beyond the limit
                cur.execute("""
                    DELETE FROM talktocity_search_history
                    WHERE user_google_id = %s
                      AND id NOT IN (
                          SELECT id FROM talktocity_search_history
                          WHERE user_google_id = %s
                          ORDER BY searched_at DESC
                          LIMIT %s
                      )
                """, (google_id, google_id, HISTORY_LIMIT))
            conn.commit()
    except Exception as e:
        logger.warning("Failed to save search history: %s", e)


# ── Schemas ────────────────────────────────────────────────────────────────

class GoogleLoginRequest(BaseModel):
    id_token: str

class SearchRequest(BaseModel):
    question: str
    city: str | None = None
    lang: str = "en"

class SearchResponse(BaseModel):
    answer: str
    sources: list[str]
    retrieval_score: float = 0.0
    chunk_count: int = 0


# ── Auth ───────────────────────────────────────────────────────────────────

@app.post("/auth/google")
async def google_login(req: GoogleLoginRequest):
    try:
        idinfo = id_token.verify_oauth2_token(
            req.id_token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=60,   # tolerate VM clock drift
        )
    except ValueError as e:
        logger.warning("Invalid Google token: %s", e)
        raise HTTPException(status_code=401, detail="Invalid Google token.")

    user = upsert_user(
        google_id=idinfo["sub"],
        email=idinfo.get("email", ""),
        name=idinfo.get("name", ""),
        picture=idinfo.get("picture", ""),
    )
    token = create_session_token(user)
    logger.info("Login: %s (%s)", user["name"], user["email"])

    return {
        "token": token,
        "user": {"email": user["email"], "name": user["name"], "picture": user["picture"]},
    }


# ── Search (protected) ────────────────────────────────────────────────────


STOPWORDS = {
    "the", "a", "an", "in", "of", "to", "for", "is", "are", "was", "were",
    "what", "where", "when", "how", "best", "good", "tell", "me", "about",
    "and", "or", "with", "from", "that", "this", "which", "do", "i", "my",
    "on", "at", "by", "as", "it", "be", "if", "than", "then", "can", "could",
    "should", "would", "into", "near", "around", "top", "things", "place", "places",
    "aur", "mein", "ki", "ke", "ka", "hai", "hain", "se", "par", "ko",
    "ek", "kya", "kaise", "mujhe", "batao", "kar", "ya"
}

TRAVEL_ASPECTS = {
    "itinerary": {"itinerary", "1-day", "1 day", "one day", "plan", "travel plan", "morning", "afternoon", "evening"},
    "food": {"food", "eat", "restaurant", "restaurants", "street food", "thali", "cafe", "cafes"},
    "sightseeing": {"visit", "places", "attractions", "tourist", "sightseeing", "landmark", "heritage", "palace", "fort", "temple", "lake"},
    "transport": {"transport", "bus", "taxi", "auto", "train", "airport", "metro", "how to reach", "get around"},
    "shopping": {"shopping", "market", "bazaar", "souvenir", "mall"},
    "stay": {"hotel", "stay", "accommodation", "hostel", "guesthouse", "resort"},
}


def _tokenize_query_terms(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) > 2]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def compute_retrieval_score(query: str, docs: list) -> tuple[float, dict]:
    """
    Returns:
      (ui_score, retrieval_debug)

    ui_score is in [0, 1] and is designed for frontend confidence labels.
    """
    if not docs:
        return 0.0, {
            "score": 0.0,
            "semantic_top3": 0.0,
            "keyword_coverage": 0.0,
            "aspect_coverage": 0.0,
            "section_diversity": 0.0,
            "matched_chunks": 0,
            "total_chunks": 0,
            "details": [],
        }

    query_terms = _tokenize_query_terms(query)
    query_lower = query.lower()

    # 1) semantic relevance: use top-3 average, not all-chunk weighted average
    semantic_top3 = 0.0
    details = []

    try:
        query_vec = embeddings.embed_query(query)
        doc_texts = [doc.page_content for doc in docs]
        doc_vecs = embeddings.embed_documents(doc_texts)

        semantic_scores = []
        for doc, doc_vec in zip(docs, doc_vecs):
            sim = max(0.0, min(1.0, _cosine_similarity(query_vec, doc_vec)))
            semantic_scores.append(sim)

        top_scores = sorted(semantic_scores, reverse=True)[:3]
        semantic_top3 = sum(top_scores) / len(top_scores) if top_scores else 0.0
    except Exception:
        semantic_scores = [0.0] * len(docs)
        semantic_top3 = 0.0

    # 2) keyword coverage
    matched_chunks = 0
    corpus_terms_hit = set()

    for idx, doc in enumerate(docs):
        text = (doc.page_content or "").lower()
        meta = getattr(doc, "metadata", {}) or {}

        matched = []
        for term in query_terms:
            if re.search(rf"\b{re.escape(term)}\b", text):
                matched.append(term)
                corpus_terms_hit.add(term)

        if matched:
            matched_chunks += 1

        details.append({
            "chunk_id": meta.get("chunk_id"),
            "section": meta.get("section"),
            "matches": matched,
            "match_count": len(matched),
            "semantic": round(semantic_scores[idx], 4) if idx < len(semantic_scores) else 0.0,
        })

    keyword_coverage = (len(corpus_terms_hit) / len(query_terms)) if query_terms else 1.0

    # 3) aspect coverage
    requested_aspects = []
    for aspect, keywords in TRAVEL_ASPECTS.items():
        if any(k in query_lower for k in keywords):
            requested_aspects.append(aspect)

    covered_aspects = 0
    if requested_aspects:
        full_text = " ".join((doc.page_content or "").lower() for doc in docs)
        for aspect in requested_aspects:
            if any(k in full_text for k in TRAVEL_ASPECTS[aspect]):
                covered_aspects += 1
        aspect_coverage = covered_aspects / len(requested_aspects)
    else:
        aspect_coverage = 1.0

    # 4) section diversity bonus
    sections = [
        (getattr(doc, "metadata", {}) or {}).get("section", "").strip().lower()
        for doc in docs
    ]
    sections = [s for s in sections if s]
    unique_sections = len(set(sections))
    if unique_sections >= 4:
        section_diversity = 1.0
    elif unique_sections == 3:
        section_diversity = 0.8
    elif unique_sections == 2:
        section_diversity = 0.6
    elif unique_sections == 1:
        section_diversity = 0.4
    else:
        section_diversity = 0.3

    # final UI score
    score = (
        0.50 * semantic_top3 +
        0.25 * keyword_coverage +
        0.20 * aspect_coverage +
        0.05 * section_diversity
    )
    score = max(0.0, min(1.0, score))
    score = round(score, 2)

    retrieval_debug = {
        "score": score,
        "semantic_top3": round(semantic_top3, 4),
        "keyword_coverage": round(keyword_coverage, 4),
        "aspect_coverage": round(aspect_coverage, 4),
        "section_diversity": round(section_diversity, 4),
        "matched_chunks": matched_chunks,
        "total_chunks": len(docs),
        "query_terms": query_terms,
        "details": details,
    }

    return score, retrieval_debug


# api.py

def detect_intent(query: str) -> str:
    q = query.lower()

    if any(term in q for term in [
        "itinerary", "1-day", "1 day", "one day",
        "day plan", "travel plan",
        "यात्रा योजना", "एक दिन", "एक दिवसीय"
    ]):
        return "itinerary"

    if any(term in q for term in [
        "food", "eat", "restaurant", "street food",
        "cafe", "dishes", "paratha",
        "खाना", "भोजन", "रेस्तरां", "कैफे"
    ]):
        return "food"

    if any(term in q for term in [
        "places", "visit", "attractions", "things to do",
        "घूमने", "दर्शनीय", "पर्यटन"
    ]):
        return "places"

    return "general"


def split_answer_and_sources(raw: str) -> tuple[str, list[str]]:
    text = raw.strip()

    # Remove leading Answer:/उत्तर:
    text = re.sub(r'^\s*(?:Answer|उत्तर):\s*', '', text, count=1, flags=re.IGNORECASE).strip()

    # Match only a final Sources/स्रोत block at the end
    match = re.search(
        r'\n(?:Sources?|स्रोत|Strot):\s*\n((?:\s*[-*]\s*.+\n?)*)\s*$',
        text,
        flags=re.IGNORECASE
    )

    if not match:
        return text, []

    answer_part = text[:match.start()].rstrip()
    sources_block = match.group(1).strip()

    ignore_list = {"<chunk_id>", "<chunk_id_1>", "<chunk_id_2>"}
    sources = []

    for line in sources_block.splitlines():
        line = line.strip()
        if line.startswith(("-", "*")):
            s = line[1:].strip()
            if s and s not in ignore_list:
                sources.append(s)

    # dedupe preserving order
    unique_sources = list(dict.fromkeys(sources))

    return answer_part, unique_sources


@app.post("/api/search", response_model=SearchResponse)
async def search(
        req: SearchRequest,
        current_user: dict = Depends(verify_session_token),  # Now returns None for guests
):
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    city = req.city.title() if req.city else None
    lang = req.lang.lower()

    # Update logging to handle anonymous users
    user_email = current_user.get("email") if current_user else "Anonymous"
    logger.info("Search | user=%s lang=%s city=%s q=%s", user_email, lang, city, question)

    try:
        k = 12

        intent = detect_intent(question)

        docs = retrieve_chunks(
            question,
            city=city,
            k=k,
            fetch_k=36,
            lambda_mult=.65,
            use_expansion=True,
            lang=lang,
            intent=intent,
        )

        if not docs:
            msg = "उपलब्ध डेटा में यह जानकारी नहीं मिली।" if lang == "hi" \
                else "Information not available in provided data."
            return SearchResponse(answer=msg, sources=[], retrieval_score=0.0, chunk_count=0)

        logger.info("Detected intent=%s", intent)
        raw = await generate_grounded_hindi_answer(question, docs, intent=intent) if lang == "hi" \
            else await generate_grounded_answer(question, docs, intent=intent)

        logger.info("RAW LEN=%s | %r", len(raw), raw)

        score, retrieval_debug = compute_retrieval_score(question, docs)
        answer_text, sources = split_answer_and_sources(raw)
        logger.info("PARSED LEN=%s | %r", len(answer_text), answer_text)

        # ── Logic Change: Only save if user is logged in ──
        if current_user:
            save_search_history(
                google_id=current_user["sub"],
                question=question, city=city, lang=lang,
                answer=answer_text, sources=sources,
                retrieval_score=score, chunk_count=len(docs)
            )
        else:
            logger.info("Guest search - skipping history save.")

        return SearchResponse(
            answer=answer_text,
            sources=sources,
            retrieval_score=score,
            chunk_count=len(docs)
        )

    except RuntimeError as e:
        logger.error("Runtime error: %s", e)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


# ── History endpoints ─────────────────────────────────────────────────────

@app.get("/api/history")
async def get_history(current_user: dict = Depends(verify_session_token)):
    """Return the last 20 searches for the logged-in user."""
    google_id = current_user["sub"]
    try:
        with psycopg.connect(RAW_DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, question, city, lang, answer, sources,
                           retrieval_score, chunk_count, searched_at
                    FROM talktocity_search_history
                    WHERE user_google_id = %s
                    ORDER BY searched_at DESC
                    LIMIT %s
                """, (google_id, HISTORY_LIMIT))
                rows = cur.fetchall()
        history = [
            {
                "id":               r[0],
                "question":         r[1],
                "city":             r[2],
                "lang":             r[3],
                "answer":           r[4],
                "sources":          r[5] or [],
                "retrieval_score":  r[6],
                "chunk_count":      r[7],
                "searched_at":      r[8].isoformat() if r[8] else None,
            }
            for r in rows
        ]
        return {"history": history}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/history/{entry_id}")
async def delete_history_entry(
    entry_id: int,
    current_user: dict = Depends(verify_session_token)
):
    """Delete a single history entry. Only the owner can delete their own entries."""
    google_id = current_user["sub"]
    try:
        with psycopg.connect(RAW_DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM talktocity_search_history
                    WHERE id = %s AND user_google_id = %s
                """, (entry_id, google_id))
                deleted = cur.rowcount
            conn.commit()
        if deleted == 0:
            raise HTTPException(status_code=404, detail="Entry not found.")
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Health ────────────────────────────────────────────────────────────────


# ── Recommendations ────────────────────────────────────────────────────────

TOPIC_BUCKETS = {
    "food":      ["food","eat","restaurant","street food","cuisine","dish","खाना","भोजन"],
    "heritage":  ["fort","palace","temple","monument","heritage","history","किला","महल","मंदिर"],
    "shopping":  ["shopping","market","buy","bazaar","mall","खरीदारी","बाज़ार"],
    "stay":      ["hotel","stay","accommodation","hostel","resort","होटल","ठहरना"],
    "transport": ["metro","bus","taxi","auto","train","airport","transport","ट्रेन","मेट्रो"],
    "nature":    ["lake","garden","park","nature","trek","waterfall","झील","पार्क"],
    "nightlife": ["cafe","coffee","bar","nightlife","rooftop","lounge","कैफे"],
    "art":       ["museum","gallery","art","culture","exhibition","संग्रहालय"],
}

SUGGESTIONS = {
    ("Delhi","food"):      ["Best street food in Chandni Chowk","Famous parathas in Delhi","Best chaat in Delhi"],
    ("Delhi","heritage"):  ["Top monuments in Delhi","Mughal history in Old Delhi","Best forts in Delhi"],
    ("Delhi","shopping"):  ["Best markets in Delhi","Shopping in Connaught Place","Sarojini Nagar guide"],
    ("Delhi","nature"):    ["Parks and gardens in Delhi","Weekend getaways from Delhi"],
    ("Delhi","nightlife"): ["Best cafes in Delhi","Rooftop restaurants in Delhi","Hauz Khas guide"],
    ("Delhi","transport"): ["Delhi metro guide","Getting around Delhi"],
    ("Delhi","stay"):      ["Best budget hotels in Delhi","Where to stay in Delhi"],
    ("Delhi","art"):       ["Best museums in Delhi","Art galleries in Delhi"],
    ("Mumbai","food"):     ["Best street food in Mumbai","Seafood restaurants Mumbai","Vada pav spots"],
    ("Mumbai","heritage"): ["Colonial heritage walk Mumbai","Historic buildings South Mumbai"],
    ("Mumbai","nightlife"):["Best cafes in Mumbai","Nightlife in Bandra","Rooftop bars Mumbai"],
    ("Mumbai","nature"):   ["Best beaches in Mumbai","Nature escapes near Mumbai"],
    ("Mumbai","shopping"): ["Shopping in Colaba Causeway","Best markets in Mumbai"],
    ("Mumbai","stay"):     ["Best hotels near Marine Drive","Budget stays in Mumbai"],
    ("Mumbai","transport"):["Mumbai local train guide","Getting around Mumbai"],
    ("Udaipur","food"):    ["Best thalis in Udaipur","Rooftop dining Udaipur","Rajasthani food Udaipur"],
    ("Udaipur","heritage"):["Palaces to visit in Udaipur","Temples in Udaipur","City Palace guide"],
    ("Udaipur","nature"):  ["Lakes to visit in Udaipur","Best viewpoints Udaipur","Fateh Sagar Lake"],
    ("Udaipur","shopping"):["Handicraft shopping Udaipur","Best markets in Udaipur"],
    ("Udaipur","nightlife"):["Best cafes in Udaipur","Lakeside restaurants Udaipur"],
    ("Udaipur","stay"):    ["Best heritage hotels Udaipur","Budget stays Udaipur"],
}


def detect_topic(text: str) -> str | None:
    text_lower = text.lower()
    for topic, keywords in TOPIC_BUCKETS.items():
        if any(k in text_lower for k in keywords):
            return topic
    return None


@app.get("/api/recommendations")
async def get_recommendations(current_user: dict = Depends(verify_session_token)):
    """
    Return up to 4 personalised chips based on search history.
    Returns [] if no history yet — frontend falls back to default trending chips.
    """
    from collections import Counter
    import random
    if not current_user:
        return {"recommendations": []}  # Guests get default trending chips on frontend
    google_id = current_user["sub"]
    try:
        with psycopg.connect(RAW_DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT question, city FROM talktocity_search_history
                    WHERE user_google_id = %s ORDER BY searched_at DESC LIMIT 20
                """, (google_id,))
                rows = cur.fetchall()
    except Exception as e:
        logger.warning("Recommendations DB error: %s", e)
        return {"recommendations": []}

    if not rows:
        return {"recommendations": []}

    city_counts  = Counter(r[1] for r in rows if r[1])
    topic_counts = Counter(t for q, _ in rows if (t := detect_topic(q)))
    top_city  = city_counts.most_common(1)[0][0]  if city_counts  else "Delhi"
    top_topic = topic_counts.most_common(1)[0][0] if topic_counts else "food"
    recent_city  = rows[0][1] if rows[0][1] else top_city
    recent_topic = detect_topic(rows[0][0]) or top_topic

    chips, seen = [], set()

    def add_chip(city, topic):
        if (city, topic) in seen: return
        opts = SUGGESTIONS.get((city, topic))
        if opts:
            q = random.choice(opts)
            chips.append({"question": q, "city": city, "lang": "en", "label": q})
            seen.add((city, topic))

    add_chip(recent_city, recent_topic)
    add_chip(top_city, top_topic)
    if len(topic_counts) > 1:
        add_chip(top_city, topic_counts.most_common(2)[1][0])
    for other in [c for c in ["Delhi", "Mumbai", "Udaipur"] if c != top_city]:
        if len(chips) >= 4: break
        add_chip(other, top_topic)

    return {"recommendations": chips[:4]}


@app.get("/health")
def health():
    from rag_core import DATABASE_URL, GEMINI_MODEL, EMBEDDING_MODEL, COLLECTION_NAME, EMBEDDING_MODEL_NAME
    
    return {
        "status": "ok",
        "ai_config": {
            "llm": GEMINI_MODEL,
            "embedding": {
                "type": EMBEDDING_MODEL,
                "model_name": EMBEDDING_MODEL_NAME,
                "collection": COLLECTION_NAME
            }
        },
        "infrastructure": {
            "database": DATABASE_URL.split("@")[-1] if DATABASE_URL else "not set",
            "auth_provider": "google_oauth2"
        }
    }