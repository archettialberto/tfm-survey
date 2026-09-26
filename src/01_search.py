#!/usr/bin/env python3
"""Search the configured sources, add manual records, and deduplicate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from common import (
    _identifiers, backfill_abstracts, deduplicate, read_csv, read_yaml, search_source, write_csv,
)


FIELDS = [
    "record_key", "source", "openalex_id", "s2_paper_id", "title", "year", "publication_date",
    "authors", "venue", "publication_type", "doi", "arxiv_id", "url",
    "citation_count_openalex", "citation_count_semantic_scholar", "retrieved_at",
    "query", "merged_records", "abstract",
]


def keep_previous_keys(records: list[dict[str, str]], previous: list[dict[str, str]]) -> int:
    """Reuse the record_key of a matching earlier record.

    A preprint that later gains a journal DOI would otherwise change its key and
    orphan every file keyed on it: screening, adjudication, categories, full texts,
    and citation keys in the paper. Earlier rows are given in priority order.
    """
    def stable(row: dict[str, str]) -> list[str]:
        # OpenAlex and Semantic Scholar IDs get reassigned between searches.
        kind, _, value = row.get("record_key", "").partition(":")
        keyed = {"doi": {"doi": value}, "arxiv": {"arxiv_id": value}}.get(kind, {})
        identifiers = _identifiers(row) | _identifiers(keyed)
        return sorted(i for i in identifiers if i.split(":")[0] in {"doi", "arxiv", "title"})

    owner = {}
    for row in previous:
        for identifier in stable(row):
            owner.setdefault(identifier, row["record_key"])
    used: dict[str, str] = {}
    kept = 0
    for row in records:
        matches = list(dict.fromkeys(owner[i] for i in stable(row) if i in owner))
        if not matches:
            continue
        if len(set(matches)) > 1:
            print(f"note: {row['record_key']} matches several earlier keys {matches}; using {matches[0]}")
        key = matches[0]
        if key in used:
            print(f"WARNING: earlier key {key} claimed by {used[key]} and {row['record_key']}")
            continue
        used[key] = row["record_key"]
        kept += key != row["record_key"]
        row["record_key"] = key
    return kept


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/search.yaml"))
    parser.add_argument("--out", type=Path, default=Path("data/records.csv"))
    parser.add_argument("--max-results", type=int, default=1000, help="Per query")
    parser.add_argument("--cache", type=Path, default=Path("data/cache"))
    parser.add_argument("--manual", type=Path, default=Path("data/manual_additions.csv"))
    parser.add_argument("--no-manual", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--no-backfill", action="store_true")
    parser.add_argument("--retry-abstracts", action="store_true",
                        help="Retry abstract lookups that previously returned nothing")
    parser.add_argument("--previous", type=Path, action="append",
                        help="Earlier file(s) with record_key whose keys are reused, highest "
                             "priority first (default: the categories and --out)")
    args = parser.parse_args()
    previous_paths = args.previous or [
        Path("data/paper_categories.csv"), args.out,
    ]
    previous = [row for path in previous_paths if path.exists() for row in read_csv(path)]

    config = read_yaml(args.config)
    search_cache = args.cache / "search"
    search_cache.mkdir(parents=True, exist_ok=True)
    raw: list[dict[str, str]] = []
    total = len(config["sources"]) * len(config["queries"])
    number = 0
    for source in config["sources"]:
        api_key = os.environ.get("OPENALEX_API_KEY" if source == "openalex" else "S2_API_KEY", "")
        for term in config["queries"]:
            number += 1
            cache_id = hashlib.sha256(
                f"{source}\0{config['cutoff_date']}\0{args.max_results}\0{term}".encode()
            ).hexdigest()[:12]
            cache_file = search_cache / f"{source}_{cache_id}.json"
            if cache_file.exists() and not args.refresh:
                rows = json.loads(cache_file.read_text(encoding="utf-8"))
            else:
                print(f"[{number}/{total}] {source}: {term}", flush=True)
                rows = search_source(source, term, config["cutoff_date"], api_key, args.max_results)
                cache_file.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            raw.extend(rows)

    if not args.no_manual and args.manual.exists():
        for row in read_csv(args.manual):
            raw.append({
                **row,
                "source": "manual",
                "paper_type": "manual",
                "query": "manual",
            })

    records, stats = deduplicate(raw)
    stats["previous_keys_reused"] = keep_previous_keys(records, previous)
    if not args.no_backfill:
        missing = sum(1 for row in records if not row.get("abstract", "").strip())
        filled = backfill_abstracts(
            records, args.cache / "abstracts.json", os.environ.get("CONTACT_EMAIL", ""),
            retry_empty=args.retry_abstracts,
        )
        stats["abstracts_backfilled"] = f"{filled}/{missing}"
    write_csv(args.out, records, FIELDS)
    print(f"Search complete: {stats}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
