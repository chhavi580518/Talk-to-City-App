"""
eval.py
-------
Batch evaluation runner for TalkToCity.
Runs all queries from queries.json through vector search and prints results.

Usage:
    python eval.py
"""

import json
from rag_core import retrieve_chunks


def main() -> None:
    with open("queries.json", "r", encoding="utf-8") as f:
        queries = json.load(f)

    print(f"Running {len(queries)} queries...\n")

    for q in queries:
        print("=" * 30)
        print("Query ID:", q["id"])
        print("City:    ", q["city"])
        print("Question:", q["query_en"])

        docs = retrieve_chunks(
            q["query_en"],
            city=q["city"],
            k=5,
            use_expansion=False,  # Raw query for eval — expansion skews results
        )

        for i, doc in enumerate(docs, 1):
            print(f"\n  Result {i}")
            print("  Title:  ", doc.metadata.get("subsection"))
            print("  Section:", doc.metadata.get("section"))
            print("  Text:   ", doc.page_content[:300])

        print()


if __name__ == "__main__":
    main()