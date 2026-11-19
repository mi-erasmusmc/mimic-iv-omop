from datetime import date
from pathlib import Path

import polars as pl
import pytest

from mimic_omop.cdm.schema import conform
from mimic_omop.cdm.spec import TABLES
from mimic_omop.export import (
    MAX_INTEGER,
    POLYMORPHIC,
    RENUMBERED_BASE,
    export_duckdb,
    id_maps,
    record_tables,
)

duckdb = pytest.importorskip("duckdb")

BIG = 5 * 10**15  # an event id as the ETL composes them


def write(root: Path, table: str, **columns) -> None:
    (root / table).mkdir(parents=True)
    conform(pl.LazyFrame(columns), table).collect().write_parquet(root / table / "part-0.parquet")


def tiny_cdm(root: Path, *, orphan: bool = False) -> None:
    day = date(2150, 1, 1)
    write(root, "person", person_id=[10000032], gender_concept_id=[8507], year_of_birth=[2100],
          race_concept_id=[0], ethnicity_concept_id=[0])  # fmt: skip
    # an admission (hadm_id) and a visit the ETL made
    write(root, "visit_occurrence", visit_occurrence_id=[20044587, 10**12 + 7],
          preceding_visit_occurrence_id=[None, 20044587], person_id=[10000032] * 2,
          visit_concept_id=[9201, 32036], visit_start_date=[day] * 2, visit_end_date=[day] * 2,
          visit_type_concept_id=[32817] * 2)  # fmt: skip
    # a susceptibility pointing at its isolate's measurement
    write(root, "measurement", measurement_id=[BIG + 20, BIG + 10],
          person_id=[10000032] * 2, measurement_concept_id=[0, 0], measurement_date=[day] * 2,
          measurement_type_concept_id=[32817] * 2, visit_occurrence_id=[10**12 + 7, 20044587],
          measurement_event_id=[None, BIG + 20], meas_event_field_concept_id=[None, 1147138])  # fmt: skip
    write(root, "specimen", specimen_id=[3584], person_id=[10000032], specimen_concept_id=[0],
          specimen_type_concept_id=[32817], specimen_date=[day])  # fmt: skip
    write(root, "fact_relationship", domain_concept_id_1=[36, 21],
          fact_id_1=[3584, BIG + (30 if orphan else 20)], domain_concept_id_2=[21, 36],
          fact_id_2=[BIG + 20, 3584], relationship_concept_id=[44818854, 44818756])  # fmt: skip


def rows(db: Path, table: str, columns: str) -> list[tuple]:
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute(f"SELECT {columns} FROM cdm.{table} ORDER BY ALL").fetchall()


def test_ids_that_do_not_fit_are_renumbered_and_references_follow(tmp_path):
    tiny_cdm(tmp_path / "cdm")
    db = tmp_path / "out.duckdb"
    export_duckdb(tmp_path / "cdm", db)
    made = RENUMBERED_BASE + 1  # the ETL's visit, after the admissions' hadm_ids
    assert rows(db, "person", "person_id") == [(10000032,)]
    assert rows(db, "visit_occurrence", "visit_occurrence_id, preceding_visit_occurrence_id") == [
        (20044587, None),
        (made, 20044587),
    ]
    # no measurement id fits: numbered from 1 in the order of the old ids
    assert rows(db, "measurement", "measurement_id, visit_occurrence_id, measurement_event_id") == [
        (1, 20044587, 2),
        (2, made, None),
    ]
    assert rows(db, "fact_relationship", "domain_concept_id_1, fact_id_1, fact_id_2") == [
        (21, 2, 3584),
        (36, 3584, 2),
    ]
    with duckdb.connect(str(db), read_only=True) as con:
        types = con.execute(
            "SELECT DISTINCT data_type FROM information_schema.columns WHERE table_schema = 'cdm'"
            " AND table_name = 'measurement' AND column_name LIKE '%id'"
        ).fetchall()
        schemas = con.execute(
            "SELECT DISTINCT table_schema FROM information_schema.tables"
        ).fetchall()
    assert types == [("INTEGER",)]
    assert schemas == [("cdm",)]  # the id maps are not written


def test_a_reference_to_a_missing_record_stops_the_export(tmp_path):
    tiny_cdm(tmp_path / "cdm", orphan=True)
    with pytest.raises(ValueError, match="fact_relationship: a value does not fit"):
        export_duckdb(tmp_path / "cdm", tmp_path / "out.duckdb")


def export_round_trips(cdm: Path, db: Path) -> None:
    """Every table of the export, its ids mapped back through id_maps, equals the parquet."""
    pk = record_tables()
    with duckdb.connect() as con:
        con.execute(f"ATTACH '{db}' AS out (READ_ONLY)")
        con.execute("CREATE SCHEMA ids")
        maps = id_maps(con, cdm, "ids")
        for table, fields in TABLES.items():
            parts = sorted((cdm / table).glob("part-*.parquet"))
            if not parts:
                continue
            exprs, joins, changed = [], [], False

            def back(column: str, ref: str, joins: list = joins) -> str:
                j = f"j{len(joins)}"
                joins.append(f"LEFT JOIN {maps[ref]} {j} ON {j}.cdm_id = x.{column}")
                return f"coalesce({j}.etl_id, x.{column})"

            for f in fields:
                expr = f"x.{f.name}"
                if pk.get(table) == f.name and table in maps:
                    expr = back(f.name, table)
                elif f.fk is not None and f.fk[0] in maps and f.fk[1] == pk.get(f.fk[0]):
                    expr = back(f.name, f.fk[0])
                elif (table, f.name) in POLYMORPHIC:
                    kind, targets = POLYMORPHIC[(table, f.name)]
                    cases = " ".join(
                        f"WHEN x.{kind} = {v} THEN "
                        f"(SELECT etl_id FROM {maps[ref]} m WHERE m.cdm_id = x.{f.name})"
                        for v, ref in targets.items()
                        if ref in maps
                    )
                    if cases:
                        expr = f"coalesce(CASE {cases} END, x.{f.name})"
                changed |= expr != f"x.{f.name}"
                exprs.append(f"{expr} AS {f.name}")
            if not changed and table not in ("person", "specimen", "visit_detail"):
                continue  # vocabulary and other tables the renumbering does not touch
            exported = f"SELECT {', '.join(exprs)} FROM out.cdm.{table} x {' '.join(joins)}"
            files = ", ".join(repr(str(p)) for p in parts)
            names = ", ".join(f.name for f in fields)
            original = f"SELECT {names} FROM read_parquet([{files}])"
            for a, b in ((exported, original), (original, exported)):
                n = con.execute(f"SELECT count(*) FROM (({a}) EXCEPT ALL ({b}))").fetchone()[0]
                assert n == 0, f"{table}: {n} rows differ"
        bad = con.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_catalog = 'out' AND column_name LIKE '%\\_id' ESCAPE '\\' "
            "AND data_type NOT IN ('INTEGER', 'VARCHAR')"
        ).fetchall()
        assert bad == []
        assert (
            con.execute("SELECT max(measurement_id) FROM out.cdm.measurement").fetchone()[0]
            <= MAX_INTEGER
        )


def test_tiny_export_round_trips(tmp_path):
    tiny_cdm(tmp_path / "cdm")
    export_duckdb(tmp_path / "cdm", tmp_path / "out.duckdb")
    export_round_trips(tmp_path / "cdm", tmp_path / "out.duckdb")


DEMO_CDM = Path(__file__).parent.parent / "data" / "demo-cdm"


@pytest.mark.demo
def test_demo_export_round_trips(tmp_path):
    if not (DEMO_CDM / "measurement").exists():
        pytest.skip("build the demo CDM into data/demo-cdm first")
    export_duckdb(DEMO_CDM, tmp_path / "demo.duckdb")
    export_round_trips(DEMO_CDM, tmp_path / "demo.duckdb")
