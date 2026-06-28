"""
rag_core.py
-----------
Shared utilities for the TalkToCity RAG pipeline.

Embedding model is selected via EMBEDDING_MODEL env var:
  EMBEDDING_MODEL=minilm  →  all-MiniLM-L6-v2  (384-dim, fast, English-optimised)
  EMBEDDING_MODEL=labse   →  LaBSE             (768-dim, slower, multilingual/Hindi)

Each model uses a separate PGVector collection so both can coexist in the DB:
  talktocity_chunks_minilm
  talktocity_chunks_labse

To switch models:
  1. Change EMBEDDING_MODEL env var
  2. Restart containers
  3. Run ingest.py (creates the new collection, leaves old one intact)
"""

import os
import re
from collections import defaultdict

import json
import logging
import httpx
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
import warnings
import requests
from typing import Optional
from dotenv import load_dotenv
from langchain_postgres import PGVector
from langchain_huggingface import HuggingFaceEmbeddings

load_dotenv()
logger = logging.getLogger(__name__)
# ── Config ──────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not set.")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not set.")

# ── Embedding model selection ───────────────────────────────────────────────

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "minilm").lower().strip()

EMBEDDING_CONFIGS = {
    "minilm": {
        "model_name":      "sentence-transformers/all-MiniLM-L6-v2",
        "collection_name": "talktocity_chunks_minilm",
        "dimensions":      384,
        "description":     "MiniLM-L6-v2 — fast, English-optimised",
    },
    "labse": {
        "model_name":      "sentence-transformers/LaBSE",
        "collection_name": "talktocity_chunks_labse",
        "dimensions":      768,
        "description":     "LaBSE — multilingual, better Hindi/EN cross-lingual retrieval",
    },
}

if EMBEDDING_MODEL not in EMBEDDING_CONFIGS:
    raise ValueError(
        f"Unknown EMBEDDING_MODEL='{EMBEDDING_MODEL}'. "
        f"Valid options: {list(EMBEDDING_CONFIGS.keys())}"
    )

_cfg = EMBEDDING_CONFIGS[EMBEDDING_MODEL]
EMBEDDING_MODEL_NAME = _cfg["model_name"]
COLLECTION_NAME      = _cfg["collection_name"]
EMBEDDING_DIMENSIONS = _cfg["dimensions"]

logger = logging.getLogger(__name__)
logger.info(
    "Embedding model: %s (%s) → collection: %s",
    EMBEDDING_MODEL, _cfg["description"], COLLECTION_NAME
)

# ── Suppress noisy warnings ─────────────────────────────────────────────────

os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
warnings.filterwarnings("ignore", message=".*position_ids.*")
logging.getLogger("transformers.modeling_utils").setLevel(logging.ERROR)

# ── Embeddings + vector store ───────────────────────────────────────────────

embeddings = HuggingFaceEmbeddings(
    model_name=EMBEDDING_MODEL_NAME,
    model_kwargs={"trust_remote_code": False},
)

# Use pre_delete_collection=False to avoid wiping existing data on startup.
# connection string must use psycopg driver (postgresql+psycopg://)
vector_store = PGVector(
    embeddings=embeddings,
    collection_name=COLLECTION_NAME,
    connection=DATABASE_URL,
    use_jsonb=True,
    pre_delete_collection=False,
    create_extension=True,
)

# ── Gemini LLM ──────────────────────────────────────────────────────────────
# Retry up to 3 times, waiting 2^x * 1 second between each retry
@retry(
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
    reraise=True
)
async def call_llm(prompt: str, temperature: float = 0.1, max_tokens: int = 4096) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta"
        f"/models/{GEMINI_MODEL}:generateContent"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "topP": 0.95,
        },
    }

    # Using httpx.AsyncClient prevents blocking the event loop
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "X-goog-api-key": GEMINI_API_KEY,
                },
                json=payload,
            )
            response.raise_for_status()

        except httpx.HTTPStatusError as e:
            logger.error("Gemini API HTTP error %s: %s", response.status_code, response.text)
            raise  # Handled by tenacity retry
        except httpx.RequestError as e:
            logger.error("Gemini API connection error: %s", str(e))
            raise  # Handled by tenacity retry

    data = response.json()

    # 1. Handle Safety / Empty Candidates Gracefully
    candidates = data.get("candidates", [])
    if not candidates:
        prompt_feedback = data.get("promptFeedback", {})
        logger.warning("No candidates returned. Feedback: %s", prompt_feedback)
        return "I'm sorry, I cannot fulfill this request due to content safety guidelines."

    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(part.get("text", "") for part in parts).strip()
    finish_reason = candidate.get("finishReason")

    logger.info(
        "Gemini finishReason=%s | parts=%s | text_len=%s",
        finish_reason, len(parts), len(text)
    )

    # 2. Check for token exhaustion
    if finish_reason == "MAX_TOKENS":
        logger.warning("Response was truncated due to maxOutputTokens limit.")

    return text

# ── Query expansion ─────────────────────────────────────────────────────────

def _load_expansion_rules() -> list[tuple[str, str]]:
    rules_path = os.path.join(os.path.dirname(__file__), "expansion_rules.json")
    if not os.path.exists(rules_path):
        logger.warning("expansion_rules.json not found — query expansion disabled")
        return []
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        compiled = [
            ("|".join(rule["patterns"]), rule["expansion"])
            for rule in data.get("rules", [])
        ]
        logger.info("Loaded %d expansion rules", len(compiled))
        return compiled
    except Exception as e:
        logger.warning("Failed to load expansion_rules.json: %s", e)
        return []


_EXPANSION_RULES: list[tuple[str, str]] = _load_expansion_rules()


def expand_query(query: str, lang: str = "en") -> str:
    q = query.lower()

    expansions = []

    if lang == "en":
        if any(t in q for t in ["itinerary", "plan", "day", "trip"]):
            expansions.append(
                "one day itinerary day plan morning afternoon evening "
                "places to visit sightseeing route must see things to do"
            )

        if any(t in q for t in ["food", "eat", "restaurant"]):
            expansions.append(
                "food places street food restaurants famous dishes local cuisine"
            )

    elif lang == "hi":
        if any(t in q for t in ["यात्रा", "दिन", "योजना"]):
            expansions.append(
                "यात्रा योजना सुबह दोपहर शाम घूमने की जगहें दर्शनीय स्थल"
            )

        if any(t in q for t in ["खाना", "भोजन"]):
            expansions.append(
                "खाना भोजन स्ट्रीट फूड प्रसिद्ध व्यंजन स्थानीय खाना"
            )

    return query + " " + " ".join(expansions) if expansions else query

# ── Retrieval ────────────────────────────────────────────────────────────────


def is_itinerary_query(query: str) -> bool:
    q = query.lower().strip()

    keyword_phrases = [
        "itinerary",
        "travel plan",
        "day plan",
        "trip plan",
        "weekend plan",
        "weekend trip",
        "day trip",
        "how to spend",
        "what to cover",
        "places to cover",
        "things to do in",
        "places to visit in",
        "यात्रा योजना",
        "घूमने की योजना",
        "दिन का प्लान",
        "ट्रिप प्लान",
        "वीकेंड प्लान",
        "वीकेंड ट्रिप",
        "डे ट्रिप",
        "क्या कवर करें",
        "कैसे बिताएं",
    ]

    if any(phrase in q for phrase in keyword_phrases):
        return True

    # flexible duration patterns
    duration_patterns = [
        r"\b\d+\s*day\b",
        r"\b\d+\s*days\b",
        r"\b\d+\-\s*day\b",
        r"\bone day\b",
        r"\btwo day\b",
        r"\bthree day\b",
        r"\bweekend\b",
        r"\b1[- ]day\b",
        r"\b2[- ]day\b",
        r"\b3[- ]day\b",
        r"\b\d+\s*दिन\b",
        r"\bएक\s*दिन\b",
        r"\bदो\s*दिन\b",
        r"\bतीन\s*दिन\b",
        r"\bएक\s*दिवसीय\b",
        r"\bवीकेंड\b",
    ]

    return any(re.search(pattern, q) for pattern in duration_patterns)

def _chunk_family(meta: dict) -> str:
    subsection = (meta.get("subsection") or "").strip().lower()
    if subsection:
        return subsection

    chunk_id = (meta.get("chunk_id") or "").strip().lower()
    if not chunk_id:
        return "unknown"

    parts = chunk_id.split("_")
    return "_".join(parts[:3]) if len(parts) >= 3 else chunk_id


def rerank_docs_for_itinerary(query: str, docs: list) -> list:
    if not is_itinerary_query(query):
        return docs

    scored = []
    family_counts = defaultdict(int)

    for idx, doc in enumerate(docs):
        text = (doc.page_content or "").lower()
        meta = doc.metadata or {}
        section = (meta.get("section") or "").strip().lower()
        subsection = (meta.get("subsection") or "").strip().lower()
        tags = [str(t).lower() for t in (meta.get("tags") or [])]

        score = 0.0

        # Prefer sightseeing / activity sections
        if section in {"see", "do", "museums"}:
            score += 4.0
        elif section in {"eat", "drink"}:
            score += 1.5
        elif section in {"buy", "sleep", "stay safe", "stay healthy"}:
            score -= 2.5
        elif section in {"understand", "get in"}:
            score -= 1.0

        # Prefer chunks that look like actual visitable places/activities
        if subsection:
            score += 1.0
        if tags:
            score += 0.5

        # Boost attraction/activity style text
        generic_positive_terms = [
            "palace", "fort", "temple", "lake", "garden", "museum",
            "haveli", "show", "sunset", "view", "park", "market",
            "attraction", "heritage", "monument", "boating", "ropeway",
            "महल", "किला", "मंदिर", "झील", "बाग", "संग्रहालय",
            "हवेली", "शो", "सूर्यास्त", "दृश्य", "पार्क", "बाजार"
        ]
        for term in generic_positive_terms:
            if term in text or term in subsection or term in tags:
                score += 0.6

        # Timings / fees often indicate a usable visitable place
        if re.search(r"\b\d{1,2}(:\d{2})?\s?(am|pm)\b", text):
            score += 0.8
        if "₹" in text or "admission" in text or "ticket" in text:
            score += 0.4

        # Penalize very generic info chunks
        generic_negative_terms = [
            "history", "climate", "introduction", "tourist information",
            "history of", "weather", "understand",
            "इतिहास", "जलवायु", "परिचय", "पर्यटक जानकारी"
        ]
        for term in generic_negative_terms:
            if term in text or term in subsection:
                score -= 0.8

        # Penalize repeated family
        family = _chunk_family(meta)
        score -= family_counts[family] * 2.0

        # Weak tie-breaker: earlier retrieval still slightly preferred
        score -= idx * 0.05

        scored.append((score, family, doc))
        family_counts[family] += 1

    scored.sort(key=lambda x: x[0], reverse=True)

    result = []
    seen_families = set()

    # First pass: maximize family diversity
    for score, family, doc in scored:
        if family not in seen_families:
            result.append(doc)
            seen_families.add(family)

    # Second pass: append leftovers if needed
    used_ids = {id(d) for d in result}
    for score, family, doc in scored:
        if id(doc) not in used_ids:
            result.append(doc)

    return result


def retrieve_chunks(
    query: str,
    city: str | None = None,
    k: int = 6,
    fetch_k: int = 18,
    lambda_mult: float = 0.65,
    use_expansion: bool = True,
    lang: str = "en",
    intent: str = "general",
) -> list:
    """
    Retrieve chunks using MMR for better diversity.

    - k: final number of chunks returned
    - fetch_k: larger candidate pool before MMR selection
    - lambda_mult:
        closer to 1.0 => more relevance
        closer to 0.0 => more diversity
    """
    retrieval_query = expand_query(query, lang=lang) if use_expansion else query
    filters = {"city": city} if city else None

    logger.info(
        "Retrieving chunks | city=%s | k=%s | fetch_k=%s | lambda=%s | original=%s | expanded=%s | collection=%s",
        city,
        k,
        fetch_k,
        lambda_mult,
        query,
        retrieval_query,
        COLLECTION_NAME,
    )

    try:
        docs = vector_store.max_marginal_relevance_search(
            retrieval_query,
            k=k,
            fetch_k=max(fetch_k, k),
            lambda_mult=lambda_mult,
            filter=filters,
        )
        if intent == "itinerary":
            docs = rerank_docs_for_itinerary(query, docs)
        return docs
    except AttributeError:
        logger.warning("MMR not available on current PGVector version, falling back to similarity_search")
        return vector_store.similarity_search(
            retrieval_query,
            k=k,
            filter=filters,
        )

# ── Context builder ──────────────────────────────────────────────────────────

def build_context(docs: list) -> str:
    parts = []
    for i, doc in enumerate(docs, 1):
        meta = doc.metadata or {}
        parts.append(
            f"[{meta.get('chunk_id', 'unknown')}]\n"
            f"{doc.page_content}\n"
        )
    return "\n\n".join(parts)

# ── Debug helpers ─────────────────────────────────────────────────────────────

def print_retrieved_sources(docs: list) -> None:
    print("\nRetrieved sources:")
    for i, doc in enumerate(docs, 1):
        meta = doc.metadata or {}
        print(
            f"{i}. "
            f"chunk_id={meta.get('chunk_id', 'unknown')}, "
            f"city={meta.get('city', 'unknown')}, "
            f"section={meta.get('section', 'unknown')}"
        )

def print_debug_chunks(docs: list) -> None:
    print("\nDEBUG CHUNK TEXT:")
    for i, doc in enumerate(docs, 1):
        meta = doc.metadata or {}
        print("\n" + "=" * 60)
        print(f"SOURCE {i}: {meta.get('chunk_id', 'unknown')}")
        print(doc.page_content[:700])
        print("=" * 60)
