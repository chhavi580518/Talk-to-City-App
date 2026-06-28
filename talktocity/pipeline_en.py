"""
pipeline_en.py
--------------
English RAG pipeline for TalkToCity.
Answers travel questions in English using grounded context from PGVector.
Includes query expansion for improved retrieval recall.

Usage (CLI):
    python pipeline_en.py
"""
import asyncio
from rag_core import (
    retrieve_chunks,
    build_context,
    call_llm,
    print_retrieved_sources,
    print_debug_chunks,
)

from prompts import get_prompt_en

async def generate_grounded_answer(question: str, docs: list, intent: str = "general") -> str:
    context = build_context(docs)

    prompt = get_prompt_en(question, context, intent)
    return await call_llm(prompt, temperature=0.1, max_tokens=4096)


def main() -> None:
    question = input("Enter question: ").strip()
    city     = input("Enter city filter (leave blank for all): ").strip()

    if not question:
        print("No question entered.")
        return

    if city:
        city = city.title()

    docs = retrieve_chunks(question, city if city else None, k=6)

    if not docs:
        print("No relevant chunks found.")
        return

    print_retrieved_sources(docs)
    print_debug_chunks(docs)

    print("\nGenerating grounded answer...\n")
    answer = asyncio.run(generate_grounded_answer(question, docs, intent="general"))

    print("Final Answer:\n")
    print(answer)


if __name__ == "__main__":
    main()