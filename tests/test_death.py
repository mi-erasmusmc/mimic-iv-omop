from datetime import date

from conftest import admissions, patients, ts

from mimic_omop import concepts as c
from mimic_omop.etl.death import death


def test_in_hospital_death_keeps_timestamp():
    df = death(
        patients({"dod": date(2150, 1, 3)}),
        admissions({"deathtime": ts("2150-01-03 04:00")}),
    ).collect()
    assert df.rows(named=True) == [
        {
            "person_id": 1,
            "death_date": date(2150, 1, 3),
            "death_datetime": ts("2150-01-03 04:00"),
            "death_type_concept_id": c.EHR,
        }
    ]


def test_out_of_hospital_death_comes_from_dod():
    df = death(patients({"dod": date(2150, 6, 1)}), admissions({})).collect()
    assert df.select("death_date", "death_datetime", "death_type_concept_id").row(0) == (
        date(2150, 6, 1),
        None,
        c.REGISTRY,
    )


def test_alive_patients_have_no_death_row():
    assert death(patients({}), admissions({})).collect().height == 0


def test_one_row_per_person_with_several_deathtimes():
    df = death(
        patients({}),
        admissions(
            {"hadm_id": 1, "deathtime": ts("2150-01-03 04:00")},
            {"hadm_id": 2, "deathtime": ts("2150-01-02 04:00")},
        ),
    ).collect()
    assert df["death_date"].to_list() == [date(2150, 1, 2)]
