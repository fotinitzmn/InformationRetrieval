from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_ES_URL = "http://localhost:9200"
DEFAULT_DOCUMENTS_INDEX = "cf_documents"
DEFAULT_INVERTED_INDEX = "cf_inverted_index"
DEFAULT_STATS_INDEX = "cf_document_stats"
DEFAULT_METADATA_INDEX = "cf_index_metadata"
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def request_json(
    url: str,
    method: str = "GET",
    data: bytes | None = None,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = Request(url, data=data, headers=headers, method=method)

    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Elasticsearch returned HTTP {exc.code} for {url}: {details}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"Could not connect to Elasticsearch at {url}. "
            "Make sure the Elasticsearch window is still running."
        ) from exc


def tokenize(text: str) -> list[str]:
    """Use exactly the same tokenization as the custom inverted index."""
    return TOKEN_PATTERN.findall(text.lower())


def mget_documents(
    es_url: str,
    index_name: str,
    document_ids: list[str],
) -> list[dict[str, Any]]:
    if not document_ids:
        return []

    body = json.dumps({"ids": document_ids}).encode("utf-8")
    response = request_json(
        f"{es_url}/{index_name}/_mget",
        method="POST",
        data=body,
    )
    return response.get("docs", [])


def load_metadata(es_url: str, metadata_index: str) -> dict[str, Any]:
    response = request_json(f"{es_url}/{metadata_index}/_doc/global")
    if not response.get("found"):
        raise RuntimeError(
            f"Global metadata record was not found in '{metadata_index}'. "
            "Build the custom inverted index first."
        )
    return response.get("_source", {})


def search_bm25(
    query: str,
    es_url: str = DEFAULT_ES_URL,
    documents_index: str = DEFAULT_DOCUMENTS_INDEX,
    inverted_index: str = DEFAULT_INVERTED_INDEX,
    stats_index: str = DEFAULT_STATS_INDEX,
    metadata_index: str = DEFAULT_METADATA_INDEX,
    top_k: int = 10,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[dict[str, Any]]:
    if top_k <= 0:
        raise ValueError("top_k must be greater than zero.")
    if k1 <= 0:
        raise ValueError("k1 must be greater than zero.")
    if not 0.0 <= b <= 1.0:
        raise ValueError("b must be between 0 and 1.")

    query_terms = list(dict.fromkeys(tokenize(query)))
    if not query_terms:
        raise ValueError("The query contains no searchable terms after tokenization.")

    metadata = load_metadata(es_url, metadata_index)
    total_documents = int(metadata["total_documents"])
    average_document_length = float(metadata["average_document_length"])
    if total_documents <= 0 or average_document_length <= 0:
        raise RuntimeError("The index metadata contains invalid collection statistics.")

    # Retrieve the posting lists for all distinct terms in the query in one request.
    term_documents = mget_documents(es_url, inverted_index, query_terms)
    contributions: dict[str, list[tuple[int, float]]] = defaultdict(list)
    candidate_ids: set[str] = set()
    matched_terms: list[str] = []

    for term_document in term_documents:
        if not term_document.get("found"):
            continue

        source = term_document.get("_source", {})
        term = str(source.get("term", term_document.get("_id", "")))
        document_frequency = int(source.get("df", 0))
        if document_frequency <= 0:
            continue

        # Robertson/Sparck Jones-style BM25 inverse document frequency.
        inverse_document_frequency = math.log(
            1.0
            + (total_documents - document_frequency + 0.5)
            / (document_frequency + 0.5)
        )
        matched_terms.append(term)

        for posting in source.get("postings", []):
            doc_id = str(posting["doc_id"])
            term_frequency = int(posting["tf"])
            if term_frequency <= 0:
                continue
            candidate_ids.add(doc_id)
            contributions[doc_id].append(
                (term_frequency, inverse_document_frequency)
            )

    if not candidate_ids:
        return []

    # Fetch document lengths for the candidate documents from Elasticsearch.
    stats_documents = mget_documents(
        es_url, stats_index, sorted(candidate_ids)
    )
    document_lengths: dict[str, int] = {}
    for stats_document in stats_documents:
        if stats_document.get("found"):
            source = stats_document.get("_source", {})
            document_lengths[str(source["doc_id"])] = int(source["doc_length"])

    scored_documents: list[dict[str, Any]] = []
    for doc_id, term_contributions in contributions.items():
        if doc_id not in document_lengths:
            continue

        document_length = document_lengths[doc_id]
        length_normalization = 1.0 - b + b * (
            document_length / average_document_length
        )

        score = 0.0
        for term_frequency, inverse_document_frequency in term_contributions:
            denominator = term_frequency + k1 * length_normalization
            score += inverse_document_frequency * (
                term_frequency * (k1 + 1.0) / denominator
            )

        scored_documents.append(
            {"doc_id": doc_id, "score": score, "doc_length": document_length}
        )

    scored_documents.sort(key=lambda item: (-item["score"], item["doc_id"]))
    top_documents = scored_documents[:top_k]

    # Fetch titles for display; scoring itself uses only the custom inverted index
    # and the document statistics stored in Elasticsearch.
    source_documents = mget_documents(
        es_url,
        documents_index,
        [item["doc_id"] for item in top_documents],
    )
    titles = {
        str(document.get("_id")): document.get("_source", {}).get("title", "")
        for document in source_documents
        if document.get("found")
    }

    for item in top_documents:
        item["title"] = titles.get(item["doc_id"], "")

    return top_documents


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search the Cystic Fibrosis collection with custom Okapi BM25."
    )
    parser.add_argument("--query", required=True, help="The query text to search for.")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--k1", type=float, default=1.5)
    parser.add_argument("--b", type=float, default=0.75)
    parser.add_argument("--es-url", default=DEFAULT_ES_URL)
    parser.add_argument("--documents-index", default=DEFAULT_DOCUMENTS_INDEX)
    parser.add_argument("--inverted-index", default=DEFAULT_INVERTED_INDEX)
    parser.add_argument("--stats-index", default=DEFAULT_STATS_INDEX)
    parser.add_argument("--metadata-index", default=DEFAULT_METADATA_INDEX)
    args = parser.parse_args()

    results = search_bm25(
        query=args.query,
        es_url=args.es_url.rstrip("/"),
        documents_index=args.documents_index,
        inverted_index=args.inverted_index,
        stats_index=args.stats_index,
        metadata_index=args.metadata_index,
        top_k=args.top_k,
        k1=args.k1,
        b=args.b,
    )

    print("=== Custom Okapi BM25 search ===")
    print(f"Query: {args.query}")
    print(f"Top results requested: {args.top_k}")
    print(f"Results found: {len(results)}")

    if not results:
        print("No documents matched the query terms.")
        return

    print("\nRank | Document ID | BM25 score | Title")
    print("-" * 100)
    for rank, result in enumerate(results, start=1):
        title = str(result["title"]).replace("\n", " ")
        print(
            f"{rank:>4} | {result['doc_id']:<11} | "
            f"{result['score']:>10.6f} | {title}"
        )


if __name__ == "__main__":
    main()
