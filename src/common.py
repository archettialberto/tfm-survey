"""Small shared helpers for the review scripts."""

from __future__ import annotations

import csv
import datetime
import html
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import yaml


def read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def clean(value: object) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def normalize_doi(value: str) -> str:
    value = clean(value).lower()
    value = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", value)
    value = re.sub(r"^doi:\s*", "", value)
    return value.rstrip(".,;:/ ")


def normalize_arxiv(value: str) -> str:
    # The digit guards and the YYMM month keep DOI suffixes such as
    # 10.1145/3770855.3816456 or 10.1016/j.x.2025.101815 from matching.
    pattern = r"(?<!\d)(?<!\d\.)(\d{2}(?:0[1-9]|1[0-2])\.\d{4,5})(?:v\d+)?(?!\d)"
    match = re.search(pattern, clean(value), re.I)
    return match.group(1) if match else ""


def arxiv_of(row: dict[str, str]) -> str:
    """arXiv ID from the dedicated field, an arXiv DOI, or an arXiv URL."""
    doi = normalize_doi(row.get("doi", ""))
    url = clean(row.get("url", ""))
    for value in (
        row.get("arxiv_id", ""),
        doi if doi.startswith("10.48550/arxiv.") else "",
        url if "arxiv.org" in url.lower() else "",
    ):
        if normalize_arxiv(value):
            return normalize_arxiv(value)
    return ""


def first_author(row: dict[str, str]) -> str:
    author = clean(row.get("authors", "")).split(";")[0]
    words = normalize_title(author).split()
    return words[-1] if words else ""


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", clean(value))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def canonical_zenodo_title(value: str) -> str:
    """Remove version/deposit wording without conflating a data deposit with a paper."""
    title = normalize_title(value)
    title = re.sub(r"\b(?:version|release|revision|preprint)\s+v?\d+(?:\.\d+)*\b", " ", title)
    title = re.sub(r"\bv\d+(?:\.\d+)+\b", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def record_key(row: dict[str, str]) -> str:
    doi = normalize_doi(row.get("doi", ""))
    arxiv = arxiv_of(row)
    if doi:
        return "doi:" + doi
    if arxiv:
        return "arxiv:" + arxiv
    return "title:" + normalize_title(row.get("title", ""))


def publication_type(raw_types: list[str], source_type: str = "", venue: str = "") -> str:
    values = {clean(value).lower().replace("_", "").replace("-", "") for value in raw_types}
    source_type = clean(source_type).lower()
    venue = clean(venue).lower()
    if "preprint" in values or "arxiv" in venue or "openreview" in venue:
        return "preprint"
    if "conference" in values or "proceedingsarticle" in values or source_type == "conference":
        return "conference"
    if values & {"journalarticle", "article", "review", "metaanalysis", "casereport", "clinicaltrial"} or source_type == "journal":
        return "journal"
    if values & {"book", "booksection", "bookchapter"}:
        return "book"
    if values & {"dissertation", "thesis"}:
        return "thesis"
    if values & {"report", "postedcontent"}:
        return "report"
    return "other" if values else "unknown"


_NOT_PUBLICATIONS = {
    "dataset", "software", "peer-review", "paratext", "editorial", "letter",
    "erratum", "retraction", "reference-entry",
}

# Generic deposit hosts and institutional repositories. These mirror work that is
# either already indexed under its real venue or was never peer reviewed, so they
# only add duplicates and self-published noise. Preprint servers (arXiv, bioRxiv,
# medRxiv, OpenReview) are deliberately absent: they carry genuine candidates.
_REPOSITORY_VENUES = (
    "zenodo", "figshare", "dspace", "mpg.pure", "freidok", "repository",
    "repositorio", "research portal", "publications student papers",
    "digital archive", "scholarship at harvard", "publication database diva",
    "le centre pour la communication scientifique directe",
)


def _is_publication(row: dict[str, str]) -> bool:
    kind = clean(row.get("paper_type") or row.get("type")).lower()
    if kind == "manual":
        return True
    title = normalize_title(row.get("title", ""))
    if kind in _NOT_PUBLICATIONS:
        return False
    if clean(row.get("publication_type")).lower() == "thesis":
        return False
    venue = clean(row.get("venue")).lower()
    if any(marker in venue for marker in _REPOSITORY_VENUES):
        return False
    prefixes = (
        "decision letter for ", "review for ", "author response for ",
        "author correction ", "data for ", "dataset for ", "code for ",
        "supplementary material for ", "raw data for ",
    )
    return len(title.split()) >= 4 and not title.startswith(prefixes)


def _identifiers(row: dict[str, str]) -> set[str]:
    keys: set[str] = set()
    doi = normalize_doi(row.get("doi", ""))
    arxiv = arxiv_of(row)
    title = normalize_title(row.get("title", ""))
    if doi:
        keys.add("doi:" + doi)
    if arxiv:
        keys.add("arxiv:" + arxiv)
    if clean(row.get("openalex_id")):
        keys.add("openalex:" + clean(row["openalex_id"]).lower())
    if clean(row.get("s2_paper_id")):
        keys.add("s2:" + clean(row["s2_paper_id"]).lower())
    if title:
        keys.add("title:" + title)
    return keys


def deduplicate(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], dict[str, int]]:
    """Deduplicate transitively and merge provenance.

    Exact DOI, version-free arXiv ID, and normalized title are strong links. Zenodo
    deposits also receive a conservative near-title pass because each uploaded
    version can have a different DOI. Non-publication deposits are removed here so
    large Zenodo data/software batches never reach screening.
    """
    kept = [dict(row) for row in rows if _is_publication(row)]
    removed_artifacts = len(rows) - len(kept)
    parent = list(range(len(kept)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        a, b = find(a), find(b)
        if a != b:
            parent[b] = a

    owner: dict[str, int] = {}
    for i, row in enumerate(kept):
        for key in _identifiers(row):
            if key in owner:
                union(i, owner[key])
            else:
                owner[key] = i

    zenodo: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, row in enumerate(kept):
        joined = " ".join((row.get("doi", ""), row.get("url", ""), row.get("venue", ""))).lower()
        if "zenodo" in joined:
            title = canonical_zenodo_title(row.get("title", ""))
            if title:
                zenodo[(title[:24], row.get("year", ""))].append(i)
    for block in zenodo.values():
        for pos, left in enumerate(block):
            a = canonical_zenodo_title(kept[left].get("title", ""))
            for right in block[pos + 1:]:
                b = canonical_zenodo_title(kept[right].get("title", ""))
                if SequenceMatcher(None, a, b).ratio() >= 0.97:
                    union(left, right)

    # Preprint and published versions of one work often differ in title casing,
    # punctuation, or a word or two, and carry unrelated DOIs.
    by_author: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(kept):
        if first_author(row):
            by_author[first_author(row)].append(i)
    for block in by_author.values():
        for pos, left in enumerate(block):
            a = normalize_title(kept[left].get("title", ""))
            for right in block[pos + 1:]:
                if find(left) == find(right):
                    continue
                b = normalize_title(kept[right].get("title", ""))
                if SequenceMatcher(None, a, b).ratio() >= 0.95:
                    union(left, right)

    groups: dict[int, list[dict[str, str]]] = defaultdict(list)
    for i, row in enumerate(kept):
        groups[find(i)].append(row)

    merged: list[dict[str, str]] = []
    for group in groups.values():
        best = max(group, key=lambda row: sum(bool(clean(v)) for v in row.values()))
        out = dict(best)
        for field, separator in (("source", "; "), ("query", "; ")):
            values = set()
            for row in group:
                values.update(part.strip() for part in row.get(field, "").split(";") if part.strip())
            out[field] = separator.join(sorted(values))
        for field in set().union(*(row.keys() for row in group)):
            if not clean(out.get(field, "")):
                out[field] = next((row.get(field, "") for row in group if clean(row.get(field, ""))), "")
        for field in ("citation_count_openalex", "citation_count_semantic_scholar"):
            counts = [int(row[field]) for row in group if str(row.get(field, "")).isdigit()]
            out[field] = str(max(counts)) if counts else ""
        out["record_key"] = record_key(out)
        out["merged_records"] = str(len(group))
        merged.append(out)

    merged.sort(key=lambda row: (row.get("publication_date", ""), normalize_title(row.get("title", ""))), reverse=True)
    stats = {
        "input": len(rows),
        "artifacts_removed": removed_artifacts,
        "duplicates_removed": len(kept) - len(merged),
        "output": len(merged),
    }
    return merged, stats


def _abstract(index: dict | None) -> str:
    words = []
    for word, positions in (index or {}).items():
        words.extend((position, word) for position in positions)
    return " ".join(word for _, word in sorted(words))


def _get_json(url: str, headers: dict[str, str], retries: int = 6) -> dict:
    error = None
    for attempt in range(retries):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            error = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500 and exc.code != 429:
                break
            if attempt < retries - 1:
                retry_after = exc.headers.get("Retry-After") if isinstance(exc, urllib.error.HTTPError) else None
                time.sleep(float(retry_after) if retry_after else min(5 * 2 ** attempt, 60))
    raise RuntimeError(f"Request failed: {error}")


def search_openalex(
    term: str, cutoff: str, api_key: str = "", max_results: int = 1000, context: str = ""
) -> list[dict[str, str]]:
    """Retrieve one query from OpenAlex using only the standard library."""
    rows: list[dict[str, str]] = []
    cursor = "*"
    while cursor and len(rows) < max_results:
        search = f'"{term}"' + (f' AND "{context}"' if context else "")
        params = {
            "filter": f"title_and_abstract.search:{search},to_publication_date:{cutoff}",
            "per-page": min(100, max_results - len(rows)),
            "cursor": cursor,
            "sort": "publication_date:desc",
        }
        if api_key:
            params["api_key"] = api_key
        url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
        data = _get_json(url, {"User-Agent": "tfn-systematic-review/1.0"})
        time.sleep(1)
        for work in data.get("results", []):
            location = work.get("primary_location") or {}
            source = location.get("source") or {}
            ids = work.get("ids") or {}
            rows.append({
                "source": "openalex",
                "openalex_id": clean(work.get("id")),
                "title": clean(work.get("title")),
                "year": str(work.get("publication_year") or ""),
                "publication_date": clean(work.get("publication_date")),
                "authors": "; ".join(clean((item.get("author") or {}).get("display_name"))
                                     for item in (work.get("authorships") or [])[:30]),
                "venue": clean(source.get("display_name")),
                "paper_type": clean(work.get("type")),
                "publication_type": publication_type(
                    [work.get("type")], source.get("type"), source.get("display_name")
                ),
                "doi": normalize_doi(work.get("doi") or ""),
                "arxiv_id": normalize_arxiv(ids.get("arxiv") or ""),
                "url": clean(work.get("doi") or location.get("landing_page_url") or work.get("id")),
                "citation_count_openalex": str(work.get("cited_by_count") or 0),
                "citation_count_semantic_scholar": "",
                "retrieved_at": datetime.date.today().isoformat(),
                "query": term,
                "abstract": _abstract(work.get("abstract_inverted_index")),
            })
        cursor = (data.get("meta") or {}).get("next_cursor") or ""
    return rows


def search_semantic_scholar(
    term: str, cutoff: str, api_key: str = "", max_results: int = 1000, context: str = ""
) -> list[dict[str, str]]:
    """Retrieve one Boolean/phrase query from Semantic Scholar's bulk endpoint."""
    rows: list[dict[str, str]] = []
    token = ""
    while len(rows) < max_results:
        query = f'"{term.replace("-", " ")}"' + (f' + "{context}"' if context else "")
        params = {
            "query": query,
            "fields": "paperId,title,abstract,year,authors,externalIds,citationCount,publicationTypes,venue,publicationDate,url",
            "publicationDateOrYear": f":{cutoff}",
            "sort": "publicationDate:desc",
            "limit": min(1000, max_results - len(rows)),
        }
        if token:
            params["token"] = token
        url = "https://api.semanticscholar.org/graph/v1/paper/search/bulk?" + urllib.parse.urlencode(params)
        headers = {"User-Agent": "tfn-systematic-review/1.0"}
        if api_key:
            headers["x-api-key"] = api_key
        data = _get_json(url, headers)
        for paper in data.get("data") or []:
            external = paper.get("externalIds") or {}
            types = paper.get("publicationTypes") or []
            rows.append({
                "source": "semantic_scholar",
                "s2_paper_id": clean(paper.get("paperId")),
                "title": clean(paper.get("title")),
                "year": str(paper.get("year") or ""),
                "publication_date": clean(paper.get("publicationDate")),
                "authors": "; ".join(clean(author.get("name")) for author in (paper.get("authors") or [])[:30]),
                "venue": clean(paper.get("venue")),
                "paper_type": clean(types[0] if types else "article"),
                "publication_type": publication_type(types, venue=paper.get("venue")),
                "doi": normalize_doi(external.get("DOI") or ""),
                "arxiv_id": normalize_arxiv(external.get("ArXiv") or ""),
                "url": clean(paper.get("url")),
                "citation_count_openalex": "",
                "citation_count_semantic_scholar": str(paper.get("citationCount") or 0),
                "retrieved_at": datetime.date.today().isoformat(),
                "query": term,
                "abstract": clean(paper.get("abstract")),
            })
        token = data.get("token") or ""
        if not token:
            break
        time.sleep(1 if api_key else 3)
    return rows


def _crossref_abstract(doi: str, mailto: str) -> str:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    agent = "tfn-systematic-review/1.0" + (f" (mailto:{mailto})" if mailto else "")
    data = _get_json(url, {"User-Agent": agent}, retries=2)
    return clean((data.get("message") or {}).get("abstract") or "")


def _europepmc_abstract(doi: str) -> str:
    url = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
        + urllib.parse.urlencode({"query": f'DOI:"{doi}"', "resultType": "core", "format": "json"})
    )
    data = _get_json(url, {"User-Agent": "tfn-systematic-review/1.0"}, retries=2)
    results = (data.get("resultList") or {}).get("result") or []
    return clean(results[0].get("abstractText") or "") if results else ""


def _arxiv_abstract(arxiv_id: str) -> str:
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": arxiv_id})
    request = urllib.request.Request(url, headers={"User-Agent": "tfn-systematic-review/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        feed = response.read().decode("utf-8", errors="replace")
    match = re.search(r"<summary>(.*?)</summary>", feed, re.S)
    return clean(match.group(1)) if match else ""


def fetch_abstract(row: dict[str, str], mailto: str = "") -> str:
    arxiv = arxiv_of(row)
    doi = normalize_doi(row.get("doi", ""))
    for lookup in (
        (lambda: _arxiv_abstract(arxiv)) if arxiv else None,
        (lambda: _crossref_abstract(doi, mailto)) if doi else None,
        (lambda: _europepmc_abstract(doi)) if doi else None,
    ):
        if lookup is None:
            continue
        try:
            abstract = lookup()
        except Exception:
            continue
        if abstract:
            return abstract
        time.sleep(0.2)
    return ""


def backfill_abstracts(
    rows: list[dict[str, str]], cache: Path, mailto: str = "", retry_empty: bool = False
) -> int:
    known = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    filled = 0
    for row in rows:
        key = normalize_doi(row.get("doi", "")) or normalize_arxiv(row.get("arxiv_id", ""))
        if clean(row.get("abstract")) or not key:
            continue
        if key not in known or (retry_empty and not known[key]):
            known[key] = fetch_abstract(row, mailto)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(known, ensure_ascii=False), encoding="utf-8")
        if known[key]:
            row["abstract"] = known[key]
            filled += 1
    return filled


def filter_query(rows: list[dict[str, str]], term: str) -> list[dict[str, str]]:
    _, separator, context = term.partition(" :: ")
    rows = [dict(row) for row in rows]
    if separator:
        required = {word.removesuffix("s") for word in normalize_title(context).split()}
        rows = [
            row for row in rows
            if required <= {
                word.removesuffix("s")
                for word in normalize_title(row.get("title", "") + " " + row.get("abstract", "")).split()
            }
        ]
    for row in rows:
        row["query"] = term
    return rows


def search_source(source: str, term: str, cutoff: str, api_key: str = "", max_results: int = 1000) -> list[dict[str, str]]:
    query, _, context = term.partition(" :: ")
    if source == "openalex":
        rows = search_openalex(query, cutoff, api_key, max_results, context)
    elif source == "semantic_scholar":
        rows = search_semantic_scholar(query, cutoff, api_key, max_results, context)
    else:
        raise ValueError(f"Unsupported source: {source}")
    return filter_query(rows, term)
