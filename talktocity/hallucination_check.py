"""
hallucination_check.py
----------------------
Checks whether TalkToCity answers are grounded in retrieved chunks
by sending both the answer and the source chunks back to Gemini
and asking it to identify any unsupported claims.

Usage:
    python hallucination_check.py
    python hallucination_check.py --lang hi
    python hallucination_check.py --limit 10
    python hallucination_check.py --city Delhi --category eat
"""

import json
import argparse
import requests
import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_URL     = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# ── Helpers ────────────────────────────────────────────────────────────────

def call_gemini(prompt: str) -> str:
    res = requests.post(
        GEMINI_URL,
        headers={"Content-Type": "application/json", "X-goog-api-key": GEMINI_API_KEY},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"temperature": 0.0, "maxOutputTokens": 512}},
        timeout=60,
    )
    res.raise_for_status()
    return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


def check_grounding(question: str, answer: str, context: str) -> dict:
    """
    Ask Gemini to check if the answer is fully supported by the context.
    Returns { grounded: bool, issues: [str], score: float }
    """
    prompt = f"""You are a fact-checking assistant. Your job is to verify whether an answer is fully supported by the provided context.

Question: {question}

Context (retrieved knowledge):
{context}

Answer to verify:
{answer}

Instructions:
1. Read the answer carefully.
2. For each claim in the answer, check if it is explicitly supported by the context.
3. List any claims that are NOT supported by the context (these are potential hallucinations).
4. If all claims are supported, say "GROUNDED".

Respond in this exact JSON format:
{{
  "grounded": true or false,
  "score": 0.0 to 1.0 (1.0 = fully grounded, 0.0 = completely hallucinated),
  "unsupported_claims": ["claim 1", "claim 2"] or []
}}

Return only valid JSON, no other text."""

    try:
        raw = call_gemini(prompt)
        # Strip markdown code fences if present
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        return {"grounded": None, "score": None, "unsupported_claims": [], "error": str(e)}


def get_answer_and_context(question: str, city: str, lang: str, k: int = 5) -> tuple[str, str]:
    """Call the local backend to get answer + retrieve the same chunks."""
    from rag_core import retrieve_chunks, build_context
    from pipeline_en import generate_grounded_answer
    from pipeline_hi import generate_grounded_hindi_answer

    docs = retrieve_chunks(question, city=city if city else None, k=k)
    if not docs:
        return "No relevant chunks found.", ""

    context = build_context(docs)
    answer = generate_grounded_hindi_answer(question, docs) if lang == "hi" \
             else generate_grounded_answer(question, docs)

    return answer, context


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Check TalkToCity answer grounding")
    parser.add_argument("--lang",     default="en",  choices=["en", "hi"])
    parser.add_argument("--city",     default=None)
    parser.add_argument("--category", default=None)
    parser.add_argument("--limit",    default=10, type=int)
    parser.add_argument("--output",   default=None)
    args = parser.parse_args()

    with open("queries.json", "r", encoding="utf-8") as f:
        queries = json.load(f)

    query_field = "query_hi" if args.lang == "hi" else "query_en"

    if args.city:
        queries = [q for q in queries if q["city"].lower() == args.city.lower()]
    if args.category:
        queries = [q for q in queries if q["category"].lower() == args.category.lower()]
    queries = queries[:args.limit]

    print(f"Checking {len(queries)} queries for hallucination | lang={args.lang}\n")

    results = []
    total_score = 0
    hallucinated = 0

    for i, q in enumerate(queries, 1):
        question = q.get(query_field, q["query_en"])
        city = q["city"]

        print(f"[{i}/{len(queries)}] {q['id']} — {question[:50]}...")

        answer, context = get_answer_and_context(question, city, args.lang)
        check = check_grounding(question, answer, context)

        score = check.get("score", 0) or 0
        grounded = check.get("grounded", False)
        issues = check.get("unsupported_claims", [])

        total_score += score
        if not grounded:
            hallucinated += 1

        status = "✓ GROUNDED" if grounded else "✗ HALLUCINATED"
        print(f"  {status} (score={score:.2f})")
        if issues:
            for issue in issues:
                print(f"    ⚠ {issue}")

        results.append({
            "id":       q["id"],
            "question": question,
            "answer":   answer,
            "score":    score,
            "grounded": grounded,
            "issues":   issues,
        })

    avg_score = total_score / len(results) if results else 0

    print(f"\n{'='*50}")
    print(f"HALLUCINATION CHECK SUMMARY")
    print(f"{'='*50}")
    print(f"Queries checked:      {len(results)}")
    print(f"Fully grounded:       {len(results) - hallucinated}/{len(results)}")
    print(f"Has hallucinations:   {hallucinated}/{len(results)}")
    print(f"Avg grounding score:  {avg_score:.2f}")

    # Worst answers
    worst = sorted(results, key=lambda r: r["score"] or 0)[:3]
    if worst:
        print(f"\nMost hallucinated answers:")
        for w in worst:
            print(f"  [{w['id']}] score={w['score']:.2f}")
            for issue in w["issues"][:2]:
                print(f"    ⚠ {issue}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({"summary": {"avg_score": avg_score, "hallucinated": hallucinated,
                                   "total": len(results)}, "results": results},
                      f, ensure_ascii=False, indent=2)
        print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
