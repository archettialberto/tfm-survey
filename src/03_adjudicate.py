#!/usr/bin/env python3
"""Apply the inclusion policy to screening results and flag records for review."""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from common import read_csv, read_yaml, write_csv

UNCLEAR = "unclear"

FIELDS = [
    "record_key", "title", "year", "publication_date", "authors", "venue",
    "publication_type", "doi", "arxiv_id", "url", "citations", "abstract",
    "model_1_tfm_role", "model_1_category", "model_1_reason",
    "model_2_tfm_role", "model_2_category", "model_2_reason",
    "record_status", "automatic_decision", "decision", "review_reason", "human_decision",
]


def policy(config: dict) -> dict[str, set[str]]:
    raw = config["adjudication"]["blacklist"]
    blacklists = {field: set(raw[field]) for field in ("tfm_role", "category")}
    allowed = {"tfm_role": set(config["tfm_role"]), "category": set(config["categories"])}
    for field, values in blacklists.items():
        unknown = values - allowed[field]
        if unknown:
            raise ValueError(f"Unknown {field} blacklist values: {sorted(unknown)}")
    return blacklists


def unclear(row: dict[str, str]) -> bool:
    return UNCLEAR in (row["tfm_role"], row["category"])


def decide(votes: list[dict[str, str]], blacklists: dict[str, set[str]]) -> str:
    if any(unclear(vote) for vote in votes):
        return "review"
    if any(
        all(vote[field] in values for vote in votes)
        for field, values in blacklists.items()
    ):
        return "exclude"
    if any(
        vote[field] in values
        for vote in votes
        for field, values in blacklists.items()
    ):
        return "review"
    return "include"


def exclusion_audit_keys(
    rows: list[dict[str, str]], fraction: float, seed: int,
) -> set[str]:
    if not 0 <= fraction <= 1:
        raise ValueError("exclude_audit_fraction must be between 0 and 1")
    candidates = sorted(
        row["record_key"] for row in rows if row["automatic_decision"] == "exclude"
    )
    if not candidates or fraction == 0:
        return set()
    sample_size = min(len(candidates), max(1, round(len(candidates) * fraction)))
    return set(random.Random(seed).sample(candidates, sample_size))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/screening.yaml"))
    parser.add_argument("--records", type=Path, default=Path("data/records.csv"))
    parser.add_argument("--screening", type=Path, default=Path("data/screening.csv"))
    parser.add_argument("--out", type=Path, default=Path("data/adjudication.csv"))
    parser.add_argument(
        "--previous", type=Path, action="append",
        help="Earlier adjudication files whose human decisions are recycled, "
             "highest priority first (default: --out)",
    )
    args = parser.parse_args()

    config = read_yaml(args.config)
    blacklists = policy(config)
    records = {row["record_key"]: row for row in read_csv(args.records)}
    assessments: dict[str, dict[str, dict[str, str]]] = {}
    for row in read_csv(args.screening):
        assessments.setdefault(row["record_key"], {})[row["model"]] = row

    previous_paths = args.previous or [args.out]
    known: set[str] = set()
    human: dict[str, str] = {}
    audited: set[str] = set()
    for path in previous_paths:
        if not path.exists():
            continue
        for row in read_csv(path):
            key = row["record_key"]
            known.add(key)
            if row.get("human_decision") and key not in human:
                human[key] = row["human_decision"]
            if row.get("review_reason") == "autoexclude_audit":
                audited.add(key)
    models = sorted({model for rows in assessments.values() for model in rows})
    if len(models) != 2:
        raise SystemExit(f"Adjudication requires exactly two screening models; found {len(models)}.")
    output = []
    for key, by_model in assessments.items():
        if key not in records or any(model not in by_model for model in models):
            continue
        votes = [by_model[model] for model in models]
        columns = {}
        for number, vote in enumerate(votes, start=1):
            for field in ("tfm_role", "category", "reason"):
                columns[f"model_{number}_{field}"] = vote[field]
        paper = records[key]
        citations = max(
            int(paper.get(field) or 0) for field in
            ("citation_count_openalex", "citation_count_semantic_scholar")
        )
        automatic_decision = decide(votes, blacklists)
        status = "existing" if key in known else "new"
        output.append({
            **{field: paper.get(field, "") for field in (
                "record_key", "title", "year", "publication_date", "authors", "venue",
                "publication_type", "doi", "arxiv_id", "url", "abstract",
            )},
            "citations": citations,
            **columns,
            "record_status": status,
            "automatic_decision": automatic_decision,
            "decision": automatic_decision,
            "review_reason": (
                "unclear" if automatic_decision == "review" and any(unclear(vote) for vote in votes)
                else "mixed_blacklist" if automatic_decision == "review"
                else ""
            ),
            "human_decision": human.get(key, ""),
        })

    # The audit sample was drawn once, at the first search. Records added by a later
    # search are all reviewed by hand, so no new sample is drawn for them.
    for row in output:
        if row["automatic_decision"] != "exclude":
            continue
        if row["record_key"] in audited:
            row["decision"] = "review"
            row["review_reason"] = "autoexclude_audit"
        elif row["record_status"] == "new":
            row["decision"] = "review"
            row["review_reason"] = "new_record"
    # Every inclusion is decided by a human, including unanimous ones.
    for row in output:
        if row["automatic_decision"] == "include" and not row["human_decision"]:
            row["decision"] = "review"
            row["review_reason"] = "unanimous_include"
    if not audited:
        adjudication = config["adjudication"]
        audit_keys = exclusion_audit_keys(
            output,
            float(adjudication.get("exclude_audit_fraction", 0)),
            int(adjudication.get("exclude_audit_seed", 0)),
        )
        for row in output:
            if row["record_key"] in audit_keys:
                row["decision"] = "review"
                row["review_reason"] = "autoexclude_audit"

    output.sort(key=lambda row: (row["decision"] != "review", row["title"]))
    write_csv(args.out, output, FIELDS)
    automatic_counts = {
        name: sum(1 for row in output if row["automatic_decision"] == name)
        for name in ("include", "exclude", "review")
    }
    pending = [row for row in output if row["decision"] == "review" and not row["human_decision"]]
    conflicts = sum(
        1 for row in output if row["human_decision"] and row["automatic_decision"] != "review"
        and row["human_decision"] != row["automatic_decision"]
    )
    print(f"Adjudication -> {args.out}")
    print(
        f"  automatic policy: {automatic_counts['include']} include, "
        f"{automatic_counts['exclude']} exclude, {automatic_counts['review']} review"
    )
    print(f"  records: {sum(r['record_status'] == 'new' for r in output)} new, "
          f"{sum(r['record_status'] == 'existing' for r in output)} existing")
    print(f"  recycled human decisions: {sum(bool(r['human_decision']) for r in output)} "
          f"({conflicts} disagree with a unanimous automatic decision)")
    print(f"  audited exclusions kept: {sum(r['review_reason'] == 'autoexclude_audit' for r in output)}")
    print(f"  pending human review: {len(pending)} "
          f"({sum(r['record_status'] == 'new' for r in pending)} new records)")


if __name__ == "__main__":
    main()
