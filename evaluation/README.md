# mimic-omop evaluation

Analyses run on MIMIC-IV and on its CDM, to check that the CDM says the same thing as the source
([plan](../docs/evaluation.md), [results](../docs/evaluation-results.md)). A separate uv project,
so that the ETL does not depend on DuckDB, model libraries or R.

```sh
cd evaluation
uv sync

# mimic-code's concept SQL, at the pinned commit (mimic_eval.mimic_code.COMMIT)
uv run mimic-eval fetch-mimic-code ../data/eval/vendor/mimic-code

# E2a: mimic-code concepts on the source and on the CDM
uv run mimic-eval e2a --source ../data/mimic-iv-3.1-parquet --cdm ../data/mimic-iv-3.1-cdm \
    --vocab ../data/vocab --mimic-code ../data/eval/vendor/mimic-code \
    --out ../data/eval/mimic-iv-3.1/e2a
```

E2a runs mimic-code's SQL unchanged, once on views over the source parquet and once on
MIMIC-shaped tables rebuilt from the CDM (`src/mimic_eval/cdm_tables.py`), and writes one CSV per
comparison to `--out`. The tests (`uv run pytest`) include an end-to-end run on the demo.
