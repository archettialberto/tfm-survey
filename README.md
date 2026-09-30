# Tabular Foundation Models: A Systematic Survey

Code and data for the survey *Tabular Foundation Models: A Systematic Survey*
by Alberto Archetti, Marina Mastroleo, Mattia Sabella, Cinzia Cappiello, and
Matteo Matteucci (Politecnico di Milano).

The repository contains the three-step pipeline that built the corpus, and the
outputs of each step as they stood for the paper (search cutoff: 15 September 2026).

## Repository layout

```
config/
  search.yaml         search sources, queries, and cutoff date
  screening.yaml      screening models, prompts, label definitions, adjudication policy
src/
  01_search.py        query OpenAlex and Semantic Scholar, add manual records, deduplicate
  02_screen.py        label every record independently with two LLMs (via OpenRouter)
  03_adjudicate.py    apply the inclusion policy and flag records for human review
  common.py           API clients, normalization, deduplication
  lit_llm.py          JSON-schema request helper for OpenRouter
data/
  records.csv           1,148 deduplicated records (1,133 from search + 15 manual)
  manual_additions.csv  the 15 manually added works
  screening.csv         two screening labels per searched record
  adjudication.csv      policy decision and final human decision per searched record
  paper_categories.csv  the 146 included works
```

Every file is keyed by `record_key`, which is the DOI (`doi:...`), the arXiv ID
(`arxiv:...`), or the normalized title (`title:...`). The same keys are the
citation keys in the paper.

The final decision for a searched record is `human_decision` when set, and
`decision` otherwise. The corpus is every record with a final decision of `include`,
plus the manual additions. It matches the rows of `paper_categories.csv`.

## Setup

Python 3.12 was used.

```bash
pip install -r requirements.txt
```

API keys are read from the environment or from a `.env` file:

| Variable             | Used by        | Required                         |
| -------------------- | -------------- | -------------------------------- |
| `OPENROUTER_API_KEY` | `02_screen.py` | yes, for new screening calls     |
| `S2_API_KEY`         | `01_search.py` | no, raises Semantic Scholar rate limits |
| `OPENALEX_API_KEY`   | `01_search.py` | no                               |
| `CONTACT_EMAIL`      | `01_search.py` | no, sent to Crossref for abstract lookups |

## Running the pipeline

Run from the repository root:

```bash
python src/01_search.py      # -> data/records.csv
python src/02_screen.py      # -> data/screening.csv
python src/03_adjudicate.py  # -> data/adjudication.csv
```

Every step reuses what is already on disk. `01_search.py` keeps the `record_key`s of
existing records. `02_screen.py` only screens `(record, model)` pairs missing from
`screening.csv`, and `--dry-run` prints the prompt without calling any model.
`03_adjudicate.py` carries over the existing human decisions. With the shipped
data, it regenerates `adjudication.csv` unchanged.

The bibliographic APIs change over time, so a fresh search will not return
exactly the same records. Use `--refresh` to bypass the local search cache in
`data/cache/`. Human decisions were entered in the `human_decision` column of
`adjudication.csv`.

## Citation

Preprint on SSRN (current version):
```
Archetti, Alberto and Mastroleo, Marina and Sabella, Mattia and Cappiello, Cinzia and
Matteucci, Matteo, Tabular Foundation Models: A Systematic Survey (September 26, 2026).
Available at SSRN: https://ssrn.com/abstract=7531718 or http://dx.doi.org/10.2139/ssrn.7531718
```
