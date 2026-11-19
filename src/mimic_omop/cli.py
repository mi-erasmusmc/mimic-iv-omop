import argparse
import logging
from datetime import date
from pathlib import Path

import polars as pl

from mimic_omop import fetch, pipeline, source, vocab
from mimic_omop.context import Release
from mimic_omop.etl.observation_period import STRATEGIES


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mimic-omop", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch-demo", help="download the MIMIC-IV demo csv.gz files from PhysioNet")
    p.add_argument("--dest", type=Path, default=Path("data/mimic-iv-demo-2.2"))

    p = sub.add_parser("fetch-cvb", help="download the pinned Tufts CVB MIMIC mappings")
    p.add_argument("--dest", type=Path, default=Path("data/vocab"))
    p.add_argument("--ref", default=vocab.CVB_REF, help="CVB git commit")

    p = sub.add_parser("load-athena", help="convert an unzipped Athena download to parquet")
    p.add_argument(
        "src", type=Path, help="directory with CONCEPT.csv, CONCEPT_RELATIONSHIP.csv, ..."
    )
    p.add_argument("--dest", type=Path, default=Path("data/vocab"))

    p = sub.add_parser("convert", help="convert MIMIC-IV csv.gz to typed parquet")
    p.add_argument("src", type=Path, help="directory containing hosp/, icu/, ...")
    p.add_argument("dest", type=Path)
    p.add_argument("--tables", nargs="*", help="e.g. hosp/patients icu/chartevents")

    p = sub.add_parser("export-duckdb", help="load the CDM parquet into a DuckDB database")
    p.add_argument("cdm", type=Path, help="output directory of `run`")
    p.add_argument("database", type=Path, help="DuckDB file to (re)create")
    p.add_argument("--schema", default="cdm")
    p.add_argument("--memory-limit", default="8GB", help="DuckDB memory limit, e.g. 8GB")

    p = sub.add_parser(
        "account", help="check that every coded source row is accounted for in a built CDM"
    )
    p.add_argument("--source", type=Path, required=True, help="converted parquet directory")
    p.add_argument("--cdm", type=Path, required=True, help="output directory of `run`")
    p.add_argument("--out", type=Path, required=True, help="directory for the results")

    p = sub.add_parser("run", help="build CDM tables as parquet")
    p.add_argument("--source", type=Path, required=True, help="converted parquet directory")
    p.add_argument("--vocab", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--tables", nargs="*", help=f"default: all of {list(pipeline.BUILDERS)}")
    p.add_argument("--buckets", type=int, default=1, help="number of person buckets")
    p.add_argument("--cdm-holder", default="not specified", help="for cdm_source.cdm_holder")
    p.add_argument("--source-version", default="", help="MIMIC-IV version, e.g. 3.1")
    p.add_argument(
        "--source-release-date",
        type=date.fromisoformat,
        help="release date of that MIMIC-IV version, YYYY-MM-DD (default: today)",
    )
    p.add_argument(
        "--observation-periods",
        choices=list(STRATEGIES),
        default="themis",
        help="observation period strategy (default: themis); see docs/etl/observation_period.md",
    )

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "fetch-demo":
        files = fetch.fetch_demo(args.dest)
        print(f"{len(files)} files in {args.dest}")
    elif args.command == "fetch-cvb":
        print(vocab.fetch_cvb(args.dest, args.ref))
    elif args.command == "load-athena":
        for table, n in vocab.load_athena(args.src, args.dest).items():
            print(f"{table}: {n} rows")
    elif args.command == "convert":
        for name, n in source.convert(args.src, args.dest, args.tables).items():
            print(f"{name}: {n} rows")
    elif args.command == "export-duckdb":
        from mimic_omop.export import export_duckdb

        counts = export_duckdb(args.cdm, args.database, args.schema, memory_limit=args.memory_limit)
        filled = {t: n for t, n in counts.items() if n}
        print(f"{len(counts)} tables in {args.database}, {len(filled)} with rows")
    elif args.command == "account":
        from mimic_omop.accounting import account

        with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=200):
            print(account(args.source, args.cdm, args.out).drop("dropped_reason"))
    elif args.command == "run":
        release = Release(
            args.cdm_holder,
            args.source_version,
            args.source_release_date,
            observation_periods=args.observation_periods,
        )
        counts = pipeline.run(args.source, args.vocab, args.out, args.tables, args.buckets, release)
        for table, n in counts.items():
            print(f"{table}: {n} rows")


if __name__ == "__main__":
    main()
