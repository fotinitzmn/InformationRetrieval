from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_ES_URL = "http://localhost:9200"
DEFAULT_DOCUMENTS_INDEX = "cf_documents"
DEFAULT_INVERTED_INDEX = "cf_inverted_index"
DEFAULT_STATS_INDEX = "cf_document_stats"
DEFAULT_METADATA_INDEX = "cf_index_metadata"
DEFAULT_BATCH_SIZE = 250

# The C.F. collection is in English. We lowercase text and keep contiguous
# alphanumeric sequences as tokens. We intentionally do not remove stopwords
# or apply stemming, so the preprocessing remains transparent and reproducible.
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def request_json(
    url: str,
    method: str = "GET",
    data: bytes | None = None,
    content_type: str | None = None,
) -> dict[str, Any]:
    headers: dict[str, str] = {}
    if content_type:
        headers["Content-Type"] = content_type

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


def index_exists(es_url: str, index_name: str) -> bool:
    request = Request(f"{es_url}/{index_name}", method="HEAD")
    try:
        with urlopen(request, timeout=15):
            return True
    except HTTPError as exc:
        if exc.code == 404:
            return False
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Could not check index '{index_name}' (HTTP {exc.code}): {details}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Could not access Elasticsearch index '{index_name}': {exc}") from exc


def tokenize(text: str) -> list[str]:
    """Convert text to lowercase alphanumeric tokens."""
    return TOKEN_PATTERN.findall(text.lower())


def read_documents_from_elasticsearch(
    es_url: str,
    documents_index: str,
) -> list[dict[str, Any]]:
    """Read the already-imported source documents from Elasticsearch."""
    query = urlencode(
        {
            "size": 2000,
            "track_total_hits": "true",
            "_source": "doc_id,content",
            "sort": "doc_id:asc",
        }
    )
    response = request_json(f"{es_url}/{documents_index}/_search?{query}")
    hits_block = response.get("hits", {})
    hits = hits_block.get("hits", [])
    total_value = hits_block.get("total", 0)
    total = total_value.get("value", 0) if isinstance(total_value, dict) else total_value

    if total != len(hits):
        raise RuntimeError(
            f"Requested all documents from '{documents_index}', but Elasticsearch "
            f"reported {total} and returned {len(hits)}. The current script fetches "
            "up to 2,000 documents in one request."
        )

    documents: list[dict[str, Any]] = []
    for hit in hits:
        source = hit.get("_source", {})
        doc_id = source.get("doc_id") or hit.get("_id")
        content = source.get("content")
        if not doc_id:
            raise RuntimeError(f"A source document has no doc_id (Elasticsearch _id={hit.get('_id')}).")
        if content is None:
            raise RuntimeError(f"Document {doc_id} has no 'content' field in Elasticsearch.")
        documents.append({"doc_id": str(doc_id), "content": str(content)})

    if not documents:
        raise RuntimeError(f"No documents were found in Elasticsearch index '{documents_index}'.")

    return documents


def build_index_data(
    documents: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Build term postings, per-document statistics, and global metadata."""
    postings_by_term: dict[str, list[dict[str, Any]]] = defaultdict(list)
    document_lengths: dict[str, int] = {}
    document_unique_terms: dict[str, int] = {}
    total_tokens = 0

    for document in documents:
        doc_id = document["doc_id"]
        tokens = tokenize(document["content"])
        term_frequencies = Counter(tokens)
        document_length = len(tokens)

        document_lengths[doc_id] = document_length
        document_unique_terms[doc_id] = len(term_frequencies)
        total_tokens += document_length

        for term, frequency in term_frequencies.items():
            postings_by_term[term].append({"doc_id": doc_id, "tf": int(frequency)})

    total_documents = len(documents)
    vocabulary_size = len(postings_by_term)
    average_document_length = (
        total_tokens / total_documents if total_documents else 0.0
    )

    # Compute each document's L2 norm for a TF-IDF vector using the same
    # smoothed IDF formula as scikit-learn's default TfidfVectorizer:
    # idf(t) = log((1 + N) / (1 + df(t))) + 1.
    norm_squared: dict[str, float] = {doc_id: 0.0 for doc_id in document_lengths}
    inverted_documents: list[dict[str, Any]] = []

    for term in sorted(postings_by_term):
        postings = sorted(postings_by_term[term], key=lambda item: item["doc_id"])
        document_frequency = len(postings)
        inverse_document_frequency = math.log(
            (1 + total_documents) / (1 + document_frequency)
        ) + 1.0

        for posting in postings:
            weight = posting["tf"] * inverse_document_frequency
            norm_squared[posting["doc_id"]] += weight * weight

        inverted_documents.append(
            {
                "term": term,
                "df": document_frequency,
                "postings": postings,
            }
        )

    stats_documents = [
        {
            "doc_id": doc_id,
            "doc_length": document_lengths[doc_id],
            "unique_terms": document_unique_terms[doc_id],
            "tfidf_norm": math.sqrt(norm_squared[doc_id]),
        }
        for doc_id in sorted(document_lengths)
    ]

    metadata = {
        "name": "cystic_fibrosis_inverted_index",
        "total_documents": total_documents,
        "vocabulary_size": vocabulary_size,
        "total_tokens": total_tokens,
        "average_document_length": average_document_length,
        "tokenization": "lowercase + regex [a-z0-9]+",
        "stopword_removal": False,
        "stemming": False,
        "vsm_weighting": "raw_tf * (log((1 + N) / (1 + df)) + 1), L2 document norm",
        "built_at": datetime.now(timezone.utc).isoformat(),
    }

    return inverted_documents, stats_documents, metadata


def create_index(es_url: str, index_name: str, properties: dict[str, Any]) -> None:
    body = {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {"properties": properties},
    }
    request_json(
        f"{es_url}/{index_name}",
        method="PUT",
        data=json.dumps(body).encode("utf-8"),
        content_type="application/json",
    )
    print(f"Created index '{index_name}'.")


def prepare_derived_indices(
    es_url: str,
    inverted_index: str,
    stats_index: str,
    metadata_index: str,
    rebuild: bool,
) -> None:
    index_names = [inverted_index, stats_index, metadata_index]
    existing = [name for name in index_names if index_exists(es_url, name)]

    if existing and not rebuild:
        raise RuntimeError(
            "The following derived index(es) already exist: "
            + ", ".join(existing)
            + ". To rebuild them from the current source documents, rerun with --rebuild."
        )

    if rebuild:
        for name in existing:
            request_json(f"{es_url}/{name}", method="DELETE")
            print(f"Deleted previous derived index '{name}'.")

    create_index(
        es_url,
        inverted_index,
        {
            "term": {"type": "keyword"},
            "df": {"type": "integer"},
            "postings": {
                "type": "nested",
                "properties": {
                    "doc_id": {"type": "keyword"},
                    "tf": {"type": "integer"},
                },
            },
        },
    )
    create_index(
        es_url,
        stats_index,
        {
            "doc_id": {"type": "keyword"},
            "doc_length": {"type": "integer"},
            "unique_terms": {"type": "integer"},
            "tfidf_norm": {"type": "double"},
        },
    )
    create_index(
        es_url,
        metadata_index,
        {
            "name": {"type": "keyword"},
            "total_documents": {"type": "integer"},
            "vocabulary_size": {"type": "integer"},
            "total_tokens": {"type": "long"},
            "average_document_length": {"type": "double"},
            "tokenization": {"type": "keyword"},
            "stopword_removal": {"type": "boolean"},
            "stemming": {"type": "boolean"},
            "vsm_weighting": {"type": "keyword"},
            "built_at": {"type": "date"},
        },
    )


def make_bulk_payload(
    documents: list[dict[str, Any]],
    index_name: str,
) -> bytes:
    lines: list[str] = []
    for document in documents:
        document_id = document.get("term") or document.get("doc_id") or "global"
        lines.append(json.dumps({"index": {"_index": index_name, "_id": document_id}}))
        lines.append(json.dumps(document, ensure_ascii=False))
    return ("\n".join(lines) + "\n").encode("utf-8")


def bulk_import(
    es_url: str,
    index_name: str,
    documents: list[dict[str, Any]],
    batch_size: int,
) -> None:
    total_batches = math.ceil(len(documents) / batch_size) if documents else 0

    for batch_number, start in enumerate(range(0, len(documents), batch_size), start=1):
        batch = documents[start : start + batch_size]
        response = request_json(
            f"{es_url}/_bulk?refresh=true",
            method="POST",
            data=make_bulk_payload(batch, index_name),
            content_type="application/x-ndjson",
        )

        if response.get("errors"):
            failures = []
            for item in response.get("items", []):
                operation = item.get("index", {})
                if "error" in operation:
                    failures.append(
                        {
                            "id": operation.get("_id"),
                            "status": operation.get("status"),
                            "error": operation["error"],
                        }
                    )
            raise RuntimeError(
                f"Bulk batch {batch_number}/{total_batches} failed. "
                f"First errors: {json.dumps(failures[:5], ensure_ascii=False, indent=2)}"
            )

        print(
            f"  {index_name}: batch {batch_number}/{total_batches} "
            f"({len(batch)} records) imported."
        )


def get_count(es_url: str, index_name: str) -> int:
    result = request_json(f"{es_url}/{index_name}/_count")
    return int(result["count"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a custom inverted index from documents stored in Elasticsearch."
    )
    parser.add_argument("--es-url", default=DEFAULT_ES_URL)
    parser.add_argument("--documents-index", default=DEFAULT_DOCUMENTS_INDEX)
    parser.add_argument("--inverted-index", default=DEFAULT_INVERTED_INDEX)
    parser.add_argument("--stats-index", default=DEFAULT_STATS_INDEX)
    parser.add_argument("--metadata-index", default=DEFAULT_METADATA_INDEX)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Delete and recreate existing derived indexes before rebuilding them.",
    )
    args = parser.parse_args()

    if args.batch_size <= 0:
        parser.error("--batch-size must be greater than zero")

    es_url = args.es_url.rstrip("/")

    info = request_json(es_url)
    version = info.get("version", {}).get("number", "unknown")
    print(f"Connected to Elasticsearch {version}.")

    if not index_exists(es_url, args.documents_index):
        raise RuntimeError(f"Source index '{args.documents_index}' does not exist.")

    documents = read_documents_from_elasticsearch(es_url, args.documents_index)
    print(f"Read {len(documents)} documents from '{args.documents_index}'.")

    inverted_documents, stats_documents, metadata = build_index_data(documents)
    print(f"Vocabulary size: {len(inverted_documents)} unique terms.")
    print(f"Total tokens: {metadata['total_tokens']}")
    print(f"Average document length: {metadata['average_document_length']:.2f} tokens.")

    prepare_derived_indices(
        es_url,
        args.inverted_index,
        args.stats_index,
        args.metadata_index,
        args.rebuild,
    )

    bulk_import(es_url, args.inverted_index, inverted_documents, args.batch_size)
    bulk_import(es_url, args.stats_index, stats_documents, args.batch_size)
    bulk_import(es_url, args.metadata_index, [metadata], args.batch_size)

    final_term_count = get_count(es_url, args.inverted_index)
    final_stats_count = get_count(es_url, args.stats_index)
    final_metadata_count = get_count(es_url, args.metadata_index)

    print("\n=== Inverted index build summary ===")
    print(f"Source documents: {len(documents)}")
    print(f"Unique terms indexed: {final_term_count}")
    print(f"Document statistics stored: {final_stats_count}")
    print(f"Global metadata records stored: {final_metadata_count}")

    if final_term_count != len(inverted_documents):
        raise RuntimeError("The final inverted-index term count does not match the built vocabulary.")
    if final_stats_count != len(documents):
        raise RuntimeError("The final document-statistics count does not match the source document count.")
    if final_metadata_count != 1:
        raise RuntimeError("Expected exactly one global metadata record.")

    print("Inverted index built and stored successfully.")


if __name__ == "__main__":
    main()
