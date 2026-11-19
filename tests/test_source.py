import gzip
from datetime import date, datetime

import polars as pl
import pytest

from mimic_omop.source import Source, convert


def write_csv_gz(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as f:
        f.write(text)


def test_convert_types_columns_by_rule(tmp_path):
    write_csv_gz(
        tmp_path / "src/hosp/prescriptions.csv.gz",
        "subject_id,starttime,ndc,dod,valuenum\n"
        "1,2150-01-01 10:00:00,00002771001,2150-02-01,1.5\n"
        "2,2150-01-01 10:00:00.250,,2150-02-01 00:00:00,\n",
    )
    convert(tmp_path / "src", tmp_path / "out")
    df = pl.read_parquet(tmp_path / "out/hosp/prescriptions.parquet")
    assert df.schema == {
        "subject_id": pl.Int64,
        "starttime": pl.Datetime("us"),
        "ndc": pl.String,
        "dod": pl.Date,
        "valuenum": pl.Float64,
    }
    assert df["ndc"].to_list() == ["00002771001", None]  # leading zeros kept, empty is null
    assert df["starttime"][1] == datetime(2150, 1, 1, 10, 0, 0, 250_000)
    assert df["dod"].to_list() == [date(2150, 2, 1)] * 2


def test_convert_lowercases_column_names(tmp_path):
    write_csv_gz(tmp_path / "src/icu/procedureevents.csv.gz", "subject_id,ORIGINALAMOUNT\n1,2\n")
    convert(tmp_path / "src", tmp_path / "out")
    assert pl.read_parquet(tmp_path / "out/icu/procedureevents.parquet").columns == [
        "subject_id",
        "originalamount",
    ]


def test_convert_fails_loudly_on_unparseable_values(tmp_path):
    write_csv_gz(tmp_path / "src/hosp/patients.csv.gz", "subject_id,anchor_age\n1,sixty\n")
    with pytest.raises(pl.exceptions.InvalidOperationError):
        convert(tmp_path / "src", tmp_path / "out")
    assert not (tmp_path / "out/hosp/patients.parquet").exists()


def test_bucket_filters_person_tables_only(tmp_path):
    (tmp_path / "hosp").mkdir()
    pl.DataFrame({"subject_id": [1, 2, 3, 4]}).write_parquet(tmp_path / "hosp/patients.parquet")
    pl.DataFrame({"itemid": [1, 2, 3]}).write_parquet(tmp_path / "hosp/d_labitems.parquet")
    src = Source(tmp_path).for_bucket(1, 2)
    assert src.scan("hosp/patients").collect()["subject_id"].to_list() == [1, 3]
    assert src.scan("hosp/d_labitems").collect().height == 3
