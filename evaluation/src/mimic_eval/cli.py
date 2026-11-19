import argparse
import logging
from pathlib import Path

import polars as pl

from mimic_eval import e2a, e2b, mimic_code, omop_meds


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="mimic-eval")
    sub = parser.add_subparsers(dest="command", required=True)

    f = sub.add_parser("fetch-mimic-code", help="check out mimic-code's concept SQL (pinned)")
    f.add_argument("dest", type=Path)

    r = sub.add_parser("e2a", help="mimic-code concepts on the source and on the CDM")
    r.add_argument("--source", type=Path, required=True, help="converted MIMIC-IV parquet")
    r.add_argument("--cdm", type=Path, required=True)
    r.add_argument("--vocab", type=Path, required=True)
    r.add_argument("--mimic-code", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)

    b = sub.add_parser("e2b", help="OHDSI cohorts on the CDM and on the source")
    b.add_argument("--source", type=Path, required=True, help="converted MIMIC-IV parquet")
    b.add_argument("--cdm", type=Path, required=True)
    b.add_argument("--vocab", type=Path, required=True)
    b.add_argument("--circe", type=Path, required=True, help="output of R/generate_cohorts.R")
    b.add_argument("--cohorts", type=int, nargs="+", required=True)
    b.add_argument("--out", type=Path, required=True)

    m = sub.add_parser("omop-meds", help="the CDM in MEDS, in the source MEDS's splits and shards")
    m.add_argument("--cdm", type=Path, required=True)
    m.add_argument("--source-meds", type=Path, required=True, help="the MIMIC-IV MEDS (layout)")
    m.add_argument("--out", type=Path, required=True)
    m.add_argument("--codes", choices=["standard", "source"], default="standard")
    m.add_argument("--diagnoses", choices=["end", "start"], default="end")
    m.add_argument("--sources", type=int, nargs="+", help="event id prefixes to keep")

    args = parser.parse_args(argv)
    if args.command == "fetch-mimic-code":
        mimic_code.fetch(args.dest)
    elif args.command == "omop-meds":
        counts = omop_meds.convert(
            args.cdm,
            args.source_meds,
            args.out,
            codes=args.codes,
            diagnoses=args.diagnoses,
            sources=args.sources,
        )
        print(counts)
    elif args.command == "e2b":
        result = e2b.run(args.cdm, args.source, args.vocab, args.circe, args.cohorts, args.out)
        with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=250):
            print(result)
    elif args.command == "e2a":
        results = e2a.run(args.source, args.cdm, args.vocab, args.mimic_code, args.out)
        with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=250):
            for name, df in results.items():
                print(name)
                print(df)
