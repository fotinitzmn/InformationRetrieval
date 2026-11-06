from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# The project root is two levels above this file: project/src/data/...
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.cfc_parser import load_documents  # noqa: E402

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "raw" / "cfc"
DEFAULT_ES_URL = "http://localhost:9200"
DEFAULT_INDEX = "cf_documents"
DEFAULT_BATCH_SIZE = 500


def request_json(url: str, method: str = "GET", data: bytes | None = None,
                 content_type: str | None = None) -> dict[str, Any]:
    headers = {}
    if content_type:
        headers["Content-Type"] = content_type

    request = Request(url, data=data, headers=headers, method=method)

    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
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


def check_connection(es_url: str, index: str) -> None:
    info = request_json(es_url)
    version = info.get("version", {}).get("number", "unknown")
    cluster = info.get("cluster_name", "unknown")
    print(f"Connected to Elasticsearch {version} (cluster: {cluster}).")

    # Verify that the target index already exists; this script does not
    # silently create an index with potentially incorrect mappings.
    index_url = f"{es_url}/{index}"
    request = Request(index_url, method="HEAD")
    try:
        with urlopen(request, timeout=10):
            pass
    except HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError(
                f"Index '{index}' does not exist. Create it before importing documents."
            ) from exc
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Could not verify index '{index}' (HTTP {exc.code}): {details}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Could not access index '{index}': {exc}") from exc

    print(f"Target index '{index}' exists.")


def make_bulk_payload(documents: list[dict[str, Any]], index: str) -> bytes:
    lines: list[str] = []

    for document in documents:
        document_id = document["id"]

        # Keep all fields produced by the parser and add doc_id, which is
        # explicitly mapped as a keyword in the Elasticsearch index.
        indexed_document = dict(document)
        indexed_document["doc_id"] = document_id

        action = {"index": {"_index": index, "_id": document_id}}
        lines.append(json.dumps(action, ensure_ascii=False))
        lines.append(json.dumps(indexed_document, ensure_ascii=False))

    # The Bulk API requires newline-delimited JSON and a final newline.
    return ("\n".join(lines) + "\n").encode("utf-8")


def import_batch(es_url: str, index: str,
                 batch: list[dict[str, Any]], batch_number: int) -> None:
    payload = make_bulk_payload(batch, index)
    response = request_json(
        f"{es_url}/_bulk?refresh=true",
        method="POST",
        data=payload,
        content_type="application/x-ndjson",
    )

    if response.get("errors"):
        failures = []
        for item in response.get("items", []):
            operation_result = item.get("index", {})
            if "error" in operation_result:
                failures.append({
                    "id": operation_result.get("_id"),
                    "status": operation_result.get("status"),
                    "error": operation_result["error"],
                })

        raise RuntimeError(
            f"Batch {batch_number} had indexing errors. "
            f"First errors: {json.dumps(failures[:5], ensure_ascii=False, indent=2)}"
        )

    print(f"Batch {batch_number}: imported {len(batch)} documents.")


def get_document_count(es_url: str, index: str) -> int:
    result = request_json(f"{es_url}/{index}/_count")
    return int(result["count"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import the Cystic Fibrosis documents into Elasticsearch."
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--es-url", default=DEFAULT_ES_URL)
    parser.add_argument("--index", default=DEFAULT_INDEX)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()

    if args.batch_size <= 0:
        parser.error("--batch-size must be greater than zero")

    es_url = args.es_url.rstrip("/")

    check_connection(es_url, args.index)

    print(f"Reading documents from: {args.data_dir}")
    documents, counts_by_file = load_documents(args.data_dir)

    print("Documents loaded from the dataset:")
    for filename, count in counts_by_file.items():
        print(f"  {filename}: {count}")
    print(f"Total documents to import: {len(documents)}")

    for batch_number, start in enumerate(
        range(0, len(documents), args.batch_size), start=1
    ):
        batch = documents[start:start + args.batch_size]
        import_batch(es_url, args.index, batch, batch_number)

    final_count = get_document_count(es_url, args.index)
    print(f"Documents currently stored in '{args.index}': {final_count}")

    if final_count < len(documents):
        raise SystemExit(
            "Import finished, but Elasticsearch contains fewer documents "
            "than the dataset. Check the index before continuing."
        )

    print("Import completed successfully.")


if __name__ == "__main__":
    main()
