"""
search_debug.py
---------------
Interactive CLI for debugging vector search results without invoking the LLM.
Useful for checking retrieval quality before running the full pipeline.

Usage:
    python search_debug.py
"""

from rag_core import retrieve_chunks, print_retrieved_sources, print_debug_chunks


def main() -> None:
    query = input("Enter query: ").strip()
    city  = input("Enter city filter (leave blank for all): ").strip()

    if not query:
        print("No query entered.")
        return

    docs = retrieve_chunks(
        query,
        city=city.title() if city else None,
        k=5,
        use_expansion=False,  # Raw query only — no expansion for debugging
    )

    if not docs:
        print("No results found.")
        return

    print_retrieved_sources(docs)
    print_debug_chunks(docs)


if __name__ == "__main__":
    main()