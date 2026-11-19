"""Load the CDM parquet output into a DuckDB database, for OHDSI tools (DQD, Achilles, ATLAS).

Every table of the CDM specification is created, with its CDM types; tables the ETL does not
build are left empty, since the tools expect all of them. The database is written in an older
DuckDB storage format (STORAGE_VERSION) so that DuckDB in R, which often lags the Python release,
can open it.

Integer fields are 32-bit INTEGER, as the CDM specifies. The parquet output keeps the ETL's
traceable 64-bit record ids (events: prefix * 10^15 + source row key * 10 + k; visits made by the
ETL, observation periods and eras are composed too), which do not fit and, above 2^53, are not
even exact as the doubles R reads them into. The export renumbers them:

- a table whose ids all fit keeps them (person_id = subject_id, visit_detail_id = transfer_id,
  specimen_id = micro_specimen_id);
- otherwise each id that does not fit gets a new one in the order of the old ids, from 1 if no id
  of the table fits, else from RENUMBERED_BASE + 1, so that admissions keep visit_occurrence_id =
  hadm_id and the visits the ETL made are told apart by their range;
- every reference follows: the foreign keys of the specification, and the polymorphic links,
  measurement_event_id / observation_event_id / note_event_id / episode_event.event_id through
  their field concept and fact_relationship's fact ids through their domain concept.

A new id is the rank of the old one, so the database keeps no record of the old ids: id_maps
recomputes them from the parquet when a record has to be traced to its source row. A value that
still does not fit after renumbering, such as a reference to a record that is not in its table,
stops the export.

DuckDB's default memory limit is 80% of the machine's memory, whatever else is running; the
export sets an explicit limit and spills to a temporary directory next to the database instead.
"""

import logging
from pathlib import Path

from mimic_omop import concepts as c
from mimic_omop.cdm.field import Field
from mimic_omop.cdm.spec import TABLES
from mimic_omop.etl.events import DOMAIN_TABLE
from mimic_omop.etl.microbiology import ID_FIELD, SPECIMEN_ID_FIELD

log = logging.getLogger(__name__)

STORAGE_VERSION = "v1.2.0"
MEMORY_LIMIT = "8GB"
MAX_INTEGER = 2**31 - 1
RENUMBERED_BASE = 10**9

# the record table each field concept and domain concept points at
FIELD_TABLE = {
    **{concept: DOMAIN_TABLE[domain] for domain, concept in ID_FIELD.items()},
    SPECIMEN_ID_FIELD: "specimen",
}
DOMAIN_CONCEPT_TABLE = {
    concept: DOMAIN_TABLE[domain] for domain, concept in c.DOMAIN_CONCEPT.items()
}
# (table, id field) -> (field naming the referenced table, its values -> table)
POLYMORPHIC = {
    ("measurement", "measurement_event_id"): ("meas_event_field_concept_id", FIELD_TABLE),
    ("observation", "observation_event_id"): ("obs_event_field_concept_id", FIELD_TABLE),
    ("note", "note_event_id"): ("note_event_field_concept_id", FIELD_TABLE),
    ("episode_event", "event_id"): ("episode_event_field_concept_id", FIELD_TABLE),
    ("fact_relationship", "fact_id_1"): ("domain_concept_id_1", DOMAIN_CONCEPT_TABLE),
    ("fact_relationship", "fact_id_2"): ("domain_concept_id_2", DOMAIN_CONCEPT_TABLE),
}


def duckdb_type(field: Field) -> str:
    if field.datatype == "integer":
        return "INTEGER"
    if field.datatype == "float":
        return "DOUBLE"
    if field.datatype == "date":
        return "DATE"
    if field.datatype == "datetime":
        return "TIMESTAMP"
    return "VARCHAR"


def record_tables() -> dict[str, str]:
    """table -> its integer primary key, for the tables whose rows the ETL numbers (all but the
    vocabulary's concept)."""
    return {
        table: f.name
        for table, fields in TABLES.items()
        for f in fields
        if f.pk and f.datatype == "integer" and table != "concept"
    }


def _fits(column: str) -> str:
    return f"({column} BETWEEN 0 AND {MAX_INTEGER})"


def parquet_source(cdm_dir: Path, table: str) -> str | None:
    """A DuckDB expression reading `table`'s parquet parts, or None if the ETL did not build it."""
    parts = sorted((Path(cdm_dir) / table).glob("part-*.parquet"))
    return f"read_parquet([{', '.join(repr(str(p)) for p in parts)}])" if parts else None


def id_maps(con, cdm_dir: Path, schema: str) -> dict[str, str]:
    """Create in `schema` the id map (etl_id, cdm_id) of every table with ids that do not fit, and
    return table -> its map. Each id that does not fit gets the next number in the order of the
    old ids, from 1 if no id of the table fits, else from RENUMBERED_BASE + 1."""
    maps = {}
    for table, pk in record_tables().items():
        src = parquet_source(cdm_dir, table)
        if src is None:
            continue
        kept_max, n_new = con.execute(
            f"SELECT max({pk}) FILTER (WHERE {_fits(pk)}), "
            f"count(*) FILTER (WHERE NOT {_fits(pk)}) FROM {src}"
        ).fetchone()
        if not n_new:
            continue
        base = 0 if kept_max is None else RENUMBERED_BASE
        if (kept_max or 0) >= RENUMBERED_BASE or base + n_new > MAX_INTEGER:
            raise ValueError(f"{table}: {n_new} ids to renumber do not fit above {kept_max}")
        maps[table] = f"{schema}.{table}"
        con.execute(
            f"CREATE TABLE {maps[table]} AS SELECT {pk} AS etl_id, "
            f"CAST({base} + row_number() OVER (ORDER BY {pk}) AS INTEGER) AS cdm_id "
            f"FROM {src} WHERE NOT {_fits(pk)}"
        )
        log.info("%s: %d ids renumbered from %d", table, n_new, base + 1)
    return maps


def export_duckdb(
    cdm_dir: Path,
    database: Path,
    schema_name: str = "cdm",
    storage_version: str = STORAGE_VERSION,
    memory_limit: str = MEMORY_LIMIT,
) -> dict[str, int]:
    import duckdb  # optional dependency: uv sync --extra duckdb

    cdm_dir, database = Path(cdm_dir), Path(database)
    if database.exists():
        database.unlink()
    spill = database.parent / f"{database.name}.tmp"
    con = duckdb.connect(config={"memory_limit": memory_limit, "temp_directory": str(spill)})
    try:
        con.execute(f"ATTACH '{database}' AS out (STORAGE_VERSION '{storage_version}')")
        con.execute(f"CREATE SCHEMA out.{schema_name}")
        con.execute("CREATE SCHEMA ids")  # in memory (spilling): not written to the database
        maps = id_maps(con, cdm_dir, "ids")

        counts = {}
        for table, fields in TABLES.items():
            columns = ", ".join(f"{f.name} {duckdb_type(f)}" for f in fields)
            target = f"out.{schema_name}.{table}"
            con.execute(f"CREATE TABLE {target} ({columns})")
            src = parquet_source(cdm_dir, table)
            if src is not None:
                try:
                    con.execute(f"INSERT INTO {target} {_select(con, table, fields, src, maps)}")
                except duckdb.ConversionException as e:
                    raise ValueError(f"{table}: a value does not fit its CDM type: {e}") from e
            counts[table] = con.execute(f"SELECT count(*) FROM {target}").fetchone()[0]
        con.execute("DETACH out")
    finally:
        con.close()
    return counts


def _select(con, table: str, fields: tuple[Field, ...], src: str, maps: dict[str, str]) -> str:
    """The SELECT of `table`'s rows, renumbered ids and the references to them replaced."""
    pk = record_tables().get(table)
    exprs, joins = [], []

    def renumbered(column: str, id_map: str, on: str = "") -> str:
        j = f"j{len(joins)}"
        joins.append(f"LEFT JOIN {id_map} {j} ON {on}{j}.etl_id = x.{column}")
        return f"coalesce({j}.cdm_id, x.{column})"

    for f in fields:
        expr = f"x.{f.name}"
        if f.name == pk and table in maps:
            expr = renumbered(f.name, maps[table])
        elif f.fk is not None and f.fk[0] in maps and f.fk[1] == record_tables()[f.fk[0]]:
            expr = renumbered(f.name, maps[f.fk[0]])
        elif (table, f.name) in POLYMORPHIC:
            kind, targets = POLYMORPHIC[(table, f.name)]
            # only the referenced ids, so the join is small even when the referenced table is not
            parts = [
                f"SELECT {value} AS kind, m.etl_id, m.cdm_id FROM {maps[ref]} m "
                f"SEMI JOIN (SELECT {f.name} AS id FROM {src} WHERE {kind} = {value}) r "
                f"ON m.etl_id = r.id"
                for value, ref in targets.items()
                if ref in maps
            ]
            if parts:
                links = f"links_{table}_{f.name}"
                con.execute(f"CREATE TEMP TABLE {links} AS {' UNION ALL '.join(parts)}")
                expr = renumbered(f.name, links, on=f"j{len(joins)}.kind = x.{kind} AND ")
        if f.datatype == "integer":
            expr = f"CAST({expr} AS INTEGER)"
        exprs.append(f"{expr} AS {f.name}")
    return f"SELECT {', '.join(exprs)} FROM {src} x {' '.join(joins)}"
