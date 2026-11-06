from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

DOCUMENT_FILES = ("cf74", "cf75", "cf76", "cf77", "cf78", "cf79")
DOCUMENT_FIELDS = {"PN", "RN", "AN", "AU", "TI", "SO", "MJ", "MN", "AB", "EX", "RF", "CT"}
QUERY_FIELDS = {"QN", "QU", "NR", "RD"}
FIELD_PATTERN = re.compile(r"^([A-Z]{2})\s+(.*)$")
QUERY_RELEVANCE_PATTERN = re.compile(r"(?<!\d)(\d{1,4})\s+([0-2]{4})(?!\d)")


def read_text(path: Path) -> str:
    # The original collection is a legacy plain-text dataset. Latin-1 safely
    # preserves its bytes and avoids decoding errors on older bibliographic text.
    return path.read_text(encoding="latin-1")


def split_records(text: str, start_pattern: re.Pattern[str]) -> list[list[str]]:
    records: list[list[str]] = []
    current: list[str] = []

    for line in text.splitlines():
        if start_pattern.match(line):
            if current:
                records.append(current)
            current = [line]
        elif current:
            current.append(line)

    if current:
        records.append(current)

    return records


def parse_fields(lines: list[str], allowed_fields: set[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    current_field: str | None = None

    for line in lines:
        match = FIELD_PATTERN.match(line)
        if match and match.group(1) in allowed_fields:
            current_field = match.group(1)
            value = " ".join(match.group(2).split())
            if current_field in fields and value:
                fields[current_field] += " " + value
            else:
                fields[current_field] = value
        elif current_field and line.strip():
            continuation = " ".join(line.split())
            fields[current_field] = (fields.get(current_field, "") + " " + continuation).strip()

    return fields


def parse_document_record(lines: list[str]) -> dict[str, Any]:
    fields = parse_fields(lines, DOCUMENT_FIELDS)

    missing = [name for name in ("PN", "RN", "AN", "TI", "SO") if not fields.get(name)]
    if missing:
        raise ValueError(f"Document record is missing required fields: {', '.join(missing)}")

    record_number = int(fields["RN"])
    content_parts = [
        fields.get("TI", ""),
        fields.get("AB", ""),
        fields.get("EX", ""),
        fields.get("MJ", ""),
        fields.get("MN", ""),
    ]
    content = "\n\n".join(part for part in content_parts if part)

    return {
        "id": f"{record_number:05d}",
        "record_number": record_number,
        "paper_number": fields["PN"],
        "medline_accession_number": fields["AN"],
        "authors": fields.get("AU", ""),
        "title": fields.get("TI", ""),
        "source": fields.get("SO", ""),
        "major_subjects": fields.get("MJ", ""),
        "minor_subjects": fields.get("MN", ""),
        "abstract": fields.get("AB", ""),
        "extract": fields.get("EX", ""),
        "references": fields.get("RF", ""),
        "citations": fields.get("CT", ""),
        "content": content,
    }


def load_documents(data_dir: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    documents: list[dict[str, Any]] = []
    counts_by_file: dict[str, int] = {}

    for filename in DOCUMENT_FILES:
        path = data_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"Dataset file not found: {path}")

        records = split_records(read_text(path), re.compile(r"^PN\s+"))
        parsed = [parse_document_record(record) for record in records]
        counts_by_file[filename] = len(parsed)
        documents.extend(parsed)

    ids = [document["id"] for document in documents]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate document IDs found in the collection.")

    return documents, counts_by_file


def parse_query_record(lines: list[str]) -> dict[str, Any]:
    fields = parse_fields(lines, QUERY_FIELDS)
    missing = [name for name in ("QN", "QU", "NR", "RD") if not fields.get(name)]
    if missing:
        raise ValueError(f"Query record is missing required fields: {', '.join(missing)}")

    relevance: list[dict[str, Any]] = []
    for raw_doc_id, raw_scores in QUERY_RELEVANCE_PATTERN.findall(fields["RD"]):
        record_number = int(raw_doc_id)
        relevance.append({
            "doc_id": f"{record_number:05d}",
            "record_number": record_number,
            "scores": {
                "rew": int(raw_scores[0]),
                "rew_colleagues": int(raw_scores[1]),
                "rew_postdocs": int(raw_scores[2]),
                "jbw": int(raw_scores[3]),
            },
        })

    expected_count = int(fields["NR"])
    if len(relevance) != expected_count:
        raise ValueError(
            f"Query {fields['QN']}: NR says {expected_count} relevance records, "
            f"but parsed {len(relevance)}."
        )

    return {
        "id": fields["QN"].zfill(5),
        "query_number": int(fields["QN"]),
        "text": fields["QU"],
        "number_of_relevant_documents": expected_count,
        "relevance": relevance,
    }


def load_queries(query_file: Path) -> list[dict[str, Any]]:
    if not query_file.is_file():
        raise FileNotFoundError(f"Query file not found: {query_file}")

    records = split_records(read_text(query_file), re.compile(r"^QN\s+"))
    queries = [parse_query_record(record) for record in records]

    ids = [query["id"] for query in queries]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate query IDs found.")

    return queries


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse and validate the Cystic Fibrosis dataset.")
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw/cfc"))
    args = parser.parse_args()

    documents, counts_by_file = load_documents(args.data_dir)
    queries = load_queries(args.data_dir / "cfquery")
    known_doc_ids = {document["id"] for document in documents}
    qrel_doc_ids = {item["doc_id"] for query in queries for item in query["relevance"]}
    unknown_qrel_ids = sorted(qrel_doc_ids - known_doc_ids)

    print("=== Cystic Fibrosis dataset validation ===")
    for filename, count in counts_by_file.items():
        print(f"{filename}: {count} documents")
    print(f"Total documents: {len(documents)}")
    print(f"Total queries: {len(queries)}")
    print(f"Total relevance judgments: {sum(len(q['relevance']) for q in queries)}")
    print(f"Unique documents appearing in relevance judgments: {len(qrel_doc_ids)}")
    print(f"Relevance judgments referencing unknown documents: {len(unknown_qrel_ids)}")
    print(f"Documents without author metadata: {sum(not d['authors'] for d in documents)}")
    print(f"Documents without major subjects: {sum(not d['major_subjects'] for d in documents)}")
    print(f"Documents without abstract/extract: {sum(not (d['abstract'] or d['extract']) for d in documents)}")
    print("\nFirst query:")
    print(f"[{queries[0]['id']}] {queries[0]['text']}")
    print("First document:")
    print(f"[{documents[0]['id']}] {documents[0]['title']}")

    if unknown_qrel_ids:
        raise SystemExit(f"Validation failed: unknown document IDs: {unknown_qrel_ids[:10]}")

    print("\nValidation completed successfully.")


if __name__ == "__main__":
    main()
