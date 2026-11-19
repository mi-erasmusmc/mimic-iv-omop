"""mimic-code's derived concepts (concepts_duckdb), run unchanged on MIMIC-shaped tables.

The same SQL files run on two sets of tables: views over the converted MIMIC-IV parquet (the
source side) and tables rebuilt from the CDM (`cdm_tables`). Each run uses a fresh DuckDB
database in which the input tables are views named as in MIMIC (`mimiciv_hosp.labevents`, ...);
the SQL writes `mimiciv_derived.*`, and the tables asked for are exported to parquet.
"""

import subprocess
from pathlib import Path

import duckdb
import polars as pl

REPO = "https://github.com/MIT-LCP/mimic-code.git"
COMMIT = "303d26c623dcc9c49cc0f204468d4acc2f063797"  # main, 2026-09-01
CONCEPTS = Path("mimic-iv/concepts_duckdb")

# derived table -> SQL file, in dependency order
CHARLSON = {"age": "demographics/age.sql", "charlson": "comorbidity/charlson.sql"}
LABS = {
    "chemistry": "measurement/chemistry.sql",
    "complete_blood_count": "measurement/complete_blood_count.sql",
    "blood_differential": "measurement/blood_differential.sql",
    "coagulation": "measurement/coagulation.sql",
    "enzyme": "measurement/enzyme.sql",
    "first_day_lab": "firstday/first_day_lab.sql",
    "kdigo_creatinine": "organfailure/kdigo_creatinine.sql",
}
VITALS = {
    "vitalsign": "measurement/vitalsign.sql",
    "first_day_vitalsign": "firstday/first_day_vitalsign.sql",
}


def fetch(dest: Path, commit: str = COMMIT) -> Path:
    """Check out mimic-code's concept SQL at a pinned commit (sparse, SQL folders only)."""
    dest = Path(dest)
    if not (dest / ".git").exists():
        subprocess.run(
            ["git", "clone", "-q", "--filter=blob:none", "--sparse", REPO, str(dest)], check=True
        )
        subprocess.run(
            ["git", "-C", str(dest), "sparse-checkout", "set", str(CONCEPTS)], check=True
        )
    subprocess.run(["git", "-C", str(dest), "fetch", "-q", "origin", commit], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "-q", commit], check=True)
    return dest


def sql_files(mimic_code: Path, *groups: dict[str, str]) -> dict[str, Path]:
    return {
        name: Path(mimic_code) / CONCEPTS / file for group in groups for name, file in group.items()
    }


def run(
    tables: dict[str, Path | pl.LazyFrame],
    files: dict[str, Path],
    out_dir: Path,
    memory_limit: str = "10GB",
) -> dict[str, Path]:
    """Run the SQL files on `tables` ({"mimiciv_hosp.labevents": parquet path or LazyFrame}) and
    write each derived table to out_dir/<name>.parquet."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db = out_dir / "work.duckdb"
    db.unlink(missing_ok=True)
    con = duckdb.connect(str(db))
    con.execute(f"SET memory_limit = '{memory_limit}'")
    con.execute(f"SET temp_directory = '{out_dir / 'duckdb-tmp'}'")
    for schema in ("mimiciv_hosp", "mimiciv_icu", "mimiciv_derived"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    for name, table in tables.items():
        if isinstance(table, pl.LazyFrame):
            path = out_dir / "tables" / f"{name}.parquet"
            path.parent.mkdir(exist_ok=True)
            table.sink_parquet(path)
        else:
            path = Path(table)
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{path}')")
    written = {}
    for name, file in files.items():
        con.execute(Path(file).read_text())
        written[name] = out_dir / f"{name}.parquet"
        con.execute(f"COPY mimiciv_derived.{name} TO '{written[name]}' (FORMAT parquet)")
    con.close()
    db.unlink()
    return written


def source_tables(parquet_dir: Path) -> dict[str, Path]:
    """Views over the converted MIMIC-IV parquet (`mimic-omop convert`)."""
    p = Path(parquet_dir)
    return {
        "mimiciv_hosp.admissions": p / "hosp/admissions.parquet",
        "mimiciv_hosp.patients": p / "hosp/patients.parquet",
        "mimiciv_hosp.diagnoses_icd": p / "hosp/diagnoses_icd.parquet",
        "mimiciv_hosp.labevents": p / "hosp/labevents.parquet",
        "mimiciv_icu.icustays": p / "icu/icustays.parquet",
        "mimiciv_icu.chartevents": p / "icu/chartevents.parquet",
    }
