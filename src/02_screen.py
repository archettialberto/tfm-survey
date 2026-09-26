#!/usr/bin/env python3
"""Annotate search records independently with two screening models."""

from __future__ import annotations

import argparse
import csv
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from tqdm import tqdm

from common import read_csv, read_yaml
from lit_llm import request_json


SCREEN_FIELDS = [
    "record_key", "model", "reason", "category", "tfm_role",
]


def schema(config: dict) -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "required": ["reason", "category", "tfm_role"],
        "properties": {
            "reason": {"type": "string"},
            "category": {"type": "string", "enum": list(config["categories"])},
            "tfm_role": {"type": "string", "enum": list(config["tfm_role"])},
        },
    }


def _options(values: dict[str, str]) -> str:
    return "\n".join(f"- {name}: {description}" for name, description in values.items())


def prompt(config: dict, row: dict[str, str]) -> str:
    paper = {key: row.get(key, "") for key in ("title", "year", "authors", "venue", "doi", "abstract")}
    return (
        f"{config['task_prompt']}\n\nScope:\n{config['scope']}"
        f"\n\nTFM role:\n{_options(config['tfm_role'])}"
        f"\n\nCategories:\n{_options(config['categories'])}"
        f"\n\nRecord:\n{json.dumps(paper, ensure_ascii=False)}"
    )


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCREEN_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def is_manual(paper: dict[str, str]) -> bool:
    return "manual" in {value.strip() for value in paper.get("source", "").split(";")}


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/screening.yaml"))
    parser.add_argument("--records", type=Path, default=Path("data/records.csv"))
    parser.add_argument("--out", type=Path, default=Path("data/screening.csv"))
    parser.add_argument("--model", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--base-url", default="https://openrouter.ai/api/v1")
    args = parser.parse_args()

    config = read_yaml(args.config)
    records = read_csv(args.records)
    if any(not paper.get("record_key") for paper in records):
        raise SystemExit("Search records are missing record_key; rerun step 01.")
    screened_records = [paper for paper in records if not is_manual(paper)]
    print(f"Screening pool: {len(screened_records)} records; {len(records) - len(screened_records)} manual additions skipped.")
    assessments = read_csv(args.out) if args.out.exists() else []
    models = args.model or config["models"]
    if len(models) != 2:
        raise SystemExit("Screening requires exactly two models.")
    if assessments and list(assessments[0]) != SCREEN_FIELDS:
        raise SystemExit("Existing screening output uses a different schema.")
    done = {(row["record_key"], row["model"]) for row in assessments}
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    work = screened_records[:args.limit] if args.limit else screened_records
    jobs = [
        (paper, model) for paper in work for model in models
        if (paper["record_key"], model) not in done
    ]
    if args.dry_run:
        if jobs:
            print(prompt(config, jobs[0][0]))
        return
    if jobs and not api_key:
        raise SystemExit("Set OPENROUTER_API_KEY or use --dry-run.")
    lock = threading.Lock()

    def screen(paper: dict[str, str], model: str) -> bool:
        try:
            answer = request_json(
                api_key=api_key, base_url=args.base_url, model=model,
                schema_name="screening", schema=schema(config), max_tokens=500,
                system_prompt=config["system_prompt"], user_prompt=prompt(config, paper),
                reasoning_effort=config.get("reasoning_effort"),
            )
            row = {
                "record_key": paper["record_key"], "model": model,
                "reason": answer["reason"], "category": answer["category"],
                "tfm_role": answer["tfm_role"],
            }
        except (RuntimeError, KeyError, TypeError) as exc:
            # Nothing is written, so re-running the script retries this record.
            print(f"FAILED {paper['record_key']} ({model}): {exc}", flush=True)
            return False
        with lock:
            append(args.out, row)
        return True

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(tqdm(
            pool.map(lambda job: screen(*job), jobs), total=len(jobs), desc="screening",
        ))
    failures = results.count(False)
    print(f"Screening results -> {args.out}")
    if failures:
        print(f"{failures} assessments failed and were not written; re-run to retry them.")


if __name__ == "__main__":
    main()
